"""Group aliases in WeChat 4.1's chat_room.ext_buffer protobuf."""


def _varint(data, pos):
    value, shift = 0, 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7f) << shift
        if byte < 0x80:
            return value, pos
        shift += 7


def _byte_fields(data):
    pos = 0
    while pos < len(data):
        tag, pos = _varint(data, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 2:
            size, pos = _varint(data, pos)
            yield field, data[pos:pos + size]
            pos += size
        elif wire == 0:
            _, pos = _varint(data, pos)
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        else:
            raise ValueError(f'未知群昵称字段类型：{wire}')


def group_member_names(buffer):
    names = {}
    for field, member in _byte_fields(buffer or b''):
        if field == 1:
            values = dict(_byte_fields(member))
            if values.get(1) and values.get(2):
                names[values[1].decode('utf-8')] = values[2].decode('utf-8')
    return names
