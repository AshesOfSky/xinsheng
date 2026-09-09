import { type ChatMessage, type Config, type Origin, type Segment, segmentText } from '../shared/types.js';

export function normalizeMessage(raw: Record<string, any>, config: Config, origin: Origin): ChatMessage | null {
  const accountId = String(raw.self_id ?? config.accountId);
  if (!accountId || accountId !== config.accountId || (raw.message_type && raw.message_type !== 'private')) return null;
  const senderId = String(raw.sender?.user_id ?? raw.user_id ?? '');
  const isSelf = senderId === accountId || raw.post_type === 'message_sent';
  const contactId = isSelf ? String(raw.target_id ?? '') : String(raw.user_id ?? senderId);
  if (contactId !== config.contactId || !senderId || raw.message_id == null) return null;
  const segments: Segment[] = [];
  let unsupported = !Array.isArray(raw.message);
  if (Array.isArray(raw.message)) {
    for (const s of raw.message) {
      if (s.type === 'text' && typeof s.data?.text === 'string') segments.push({ type: 'text', data: { text: s.data.text } });
      else if (s.type === 'face' && s.data?.id != null) segments.push({ type: 'face', data: { id: String(s.data.id) } });
      else unsupported = true;
    }
  } else if (typeof raw.message === 'string') segments.push({ type: 'text', data: { text: raw.message } });
  const messageId = String(raw.message_id);
  return {
    id: accountId + ':' + contactId + ':' + messageId, accountId, contactId, messageId,
    sequence: raw.real_seq == null ? '' : String(raw.real_seq), senderId,
    time: Number(raw.time) * 1000 || Date.now(), segments,
    text: segmentText(segments) + (unsupported ? ' [含未支持的消息内容]' : ''),
    unsupported, author: isSelf ? 'human' : 'contact', origin,
  };
}
