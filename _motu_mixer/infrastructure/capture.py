def parse_amidi_hex(text: str) -> bytes:
    """Bytes of an `amidi -d` hex capture, line by line in file order, blank lines skipped."""
    return b"".join(bytes.fromhex(line) for line in text.splitlines() if line.strip())
