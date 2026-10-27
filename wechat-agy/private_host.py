"""Serve one explicitly selected private chat alongside the group bot."""
import logging
import time

from native_input import chat_input, editor_empty, send_text
from private_chat import PrivateConversation, private_model_message
from reply_router import ResearchUnavailable


class PrivateTargetUnavailable(RuntimeError):
    """No input has been attempted; the incoming message may be retried."""


def file_send_hold(bot, contact):
    """A possible file draft pauses this contact until an operator checks it."""
    return bot.data / 'private' / contact['id'] / 'file-send-pending'


def send_private(bridge, contact, text):
    """Send only to the already-open dedicated window for this exact wxid."""
    import win32gui
    from wechatauto.guia import WeChatGUI

    # On WeChat 4.1.15 the Win32 class is Qt51514QWindowIcon; the stable
    # ChatSingleWindow+wxid identity is exposed by UI Automation instead.
    try:
        hwnd = win32gui.FindWindow(None, contact['name'])
        if not hwnd:
            raise PrivateTargetUnavailable('目标私聊独立窗口未打开，未发送')
        gui = WeChatGUI(hwnd=hwnd)
        if gui._get_db().account != bridge.config['account']:
            raise PrivateTargetUnavailable('私聊窗口账号与监听账号不同，未发送')
        uia = gui._get_uia()
        if uia is None or uia._win.AutomationId != 'ChatSingleWindow' + contact['id']:
            raise PrivateTargetUnavailable('私聊窗口身份与指定微信 ID 不一致，未发送')
        edit = chat_input(uia, contact['name'])
        if not editor_empty(uia, edit):
            raise PrivateTargetUnavailable('私聊输入框已有草稿或无法确认为空，未覆盖或发送')
    except PrivateTargetUnavailable:
        raise
    except Exception as exc:
        raise PrivateTargetUnavailable(f'私聊窗口暂不可用，未发送：{exc}') from exc
    return send_text(uia, text, contact['name'])


def confirm_private(chat, text, started):
    """Check the account-bound database rather than assuming a click sent."""
    for row in chat.db.get_messages(chat.contact_id, limit=30):
        if (row.get('sender_username') == chat.self_id and row.get('type') == '文本'
                and row.get('content') == text and row.get('create_time', 0) >= started - 2):
            return True
    return False


def private_loop(bot, contact):
    import pythoncom

    pythoncom.CoInitialize()
    chat = None
    pending = []
    try:
        chat = PrivateConversation(bot.bridge.db, bot.data.parent, contact['id'], contact['name'],
                                   relationship=contact.get('relationship'))
        with bot.private_sessions_lock:
            bot.private_sessions[contact['id']] = chat
        chat.start()
        pending = chat.pending()
        draft = None
        file_coordinator = None
        bot.update_private_status(contact['id'], private_ready=True, private_state='ready', private_error=None)
        while not bot.stop.is_set():
            deferred = []
            try:
                new_messages = chat.poll()
                if new_messages:
                    pending.extend(new_messages)
                    draft = None
                if not pending:
                    bot.stop.wait(bot.config['poll_seconds'])
                    continue
                if contact.get('relationship') == 'girlfriend':
                    if file_coordinator is None and any(m['kind'] == '文件/链接/卡片' for m in pending):
                        from private_file_host import FileCoordinator
                        file_coordinator = FileCoordinator(bot, chat, contact)
                    if file_coordinator:
                        pending, deferred, changed = file_coordinator.step(pending)
                        if changed:
                            draft = None
                            continue
                        if not pending:
                            bot.stop.wait(bot.config['poll_seconds'])
                            continue
                context = [private_model_message(m) for m in chat.archive.recent(8)]
                blocked, _ = chat.guard.filter([private_model_message(m) for m in pending], context)
                if blocked:
                    ignored = [m for m in pending if m['id'] in blocked]
                    chat.ack(ignored)
                    pending = [m for m in pending if m['id'] not in blocked]
                    draft = None
                    bot.update_private_status(contact['id'], private_state='ready', private_error=None,
                                      private_decision='涉政话题保持安静')
                    if not pending:
                        continue
                batch = list(pending)
                if draft is None:
                    bot.update_private_status(contact['id'], private_state='generating')
                    result = chat.generate(batch, (bot.data.parent / 'PERSONA.md').read_text(encoding='utf-8'))
                    bot.update_private_status(contact['id'], private_last_model=chat.last_model)
                    # Private work may take a while; fold in new instructions before sending.
                    new_messages = chat.poll()
                    if new_messages:
                        pending.extend(new_messages)
                        continue
                    draft = result['text'] if result['reply'] and result['text'] else (
                        '大肥鱼在呢，你把具体需要处理的内容发我，我帮你看看。')
                    context = [private_model_message(m) for m in chat.archive.recent(8)]
                    blocked, blocked_draft = chat.guard.filter(
                        [private_model_message(m) for m in batch], context, draft=draft)
                    if blocked or blocked_draft:
                        ignored = batch if blocked_draft else [m for m in batch if m['id'] in blocked]
                        chat.ack(ignored)
                        ignored_ids = {m['id'] for m in ignored}
                        pending = [m for m in pending if m['id'] not in ignored_ids]
                        draft = None
                        bot.update_private_status(contact['id'], private_state='ready', private_error=None,
                                          private_decision='涉政话题保持安静')
                        continue
                if bot.stop.is_set():
                    break
                if file_send_hold(bot, contact).exists():
                    bot.update_private_status(contact['id'], private_state='error',
                        private_error='文件发送状态待核，请检查私聊草稿和消息后恢复')
                    bot.stop.wait(3)
                    continue
                with bot.send_lock:
                    if bot.stop.is_set():
                        break
                    if file_send_hold(bot, contact).exists():
                        # Nothing was submitted. Leave this batch queued.
                        continue
                    chat.claim(batch)
                    try:
                        outbox_id = chat.archive.record_outgoing(batch[-1]['id'], draft, 'sending')
                    except Exception:
                        chat.release(batch)
                        raise
                    started = time.time()
                    try:
                        ok, detail = send_private(bot.bridge, contact, draft)
                    except PrivateTargetUnavailable as exc:
                        chat.archive.outgoing_status(outbox_id, 'cancelled')
                        chat.release(batch)
                        ok, detail = None, str(exc)
                    except Exception as exc:
                        ok, detail = False, str(exc)
                if ok is None:
                    bot.update_private_status(contact['id'], private_state='error', private_error=detail)
                    bot.stop.wait(3)
                    continue
                # Do not resend after an ambiguous click; this avoids duplicates.
                pending = pending[len(batch):]
                chat.ack(batch)
                if not ok:
                    chat.archive.outgoing_status(outbox_id, 'unconfirmed')
                    bot.update_private_status(contact['id'], private_state='error', private_error=detail)
                    draft = None
                    continue
                confirmed = False
                for _ in range(40):
                    if confirm_private(chat, draft, started):
                        confirmed = True
                        break
                    if bot.stop.wait(0.5):
                        break
                chat.archive.outgoing_status(outbox_id, 'confirmed' if confirmed else 'unconfirmed')
                bot.update_private_status(contact['id'], private_state='ready' if confirmed else 'error',
                                  private_error=None if confirmed else '私聊发送后未能回读确认')
                logging.info('private reply %s', 'confirmed' if confirmed else 'unconfirmed')
                draft = None
            except Exception as exc:
                if bot.stop.is_set():
                    break
                logging.exception('private reply failed')
                bot.update_private_status(contact['id'], private_state='error', private_error=str(exc))
                bot.stop.wait(30 if isinstance(exc, ResearchUnavailable) or chat.last_model == 'gpt-6-astra' else 3)
            finally:
                pending.extend(deferred)
    except Exception as exc:
        if not bot.stop.is_set():
            logging.exception('private listener failed to start')
            bot.update_private_status(contact['id'], private_ready=False, private_state='error', private_error=str(exc))
    finally:
        if chat:
            with bot.private_sessions_lock:
                bot.private_sessions.pop(contact['id'], None)
            chat.close()
        pythoncom.CoUninitialize()
