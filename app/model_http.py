"""Read model JSON with wire and decoded byte limits before parsing it."""

import json
import zlib

import httpx

GENERATION_RESPONSE_BYTES = 1_000_000
EMBEDDING_RESPONSE_BYTES = 8_000_000
WIRE_OVERHEAD_BYTES = 65_536


def post_json(url, *, max_bytes, headers, **options):
    # Advertising only these encodings lets us bound decompression itself.
    headers = {**headers, "Accept-Encoding": "gzip, deflate"}
    with httpx.stream("POST", url, headers=headers, **options) as response:
        response.raise_for_status()
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in {"identity", "gzip", "deflate"}:
            raise ValueError("Unsupported model response encoding.")
        wire_limit = max_bytes + (WIRE_OVERHEAD_BYTES if encoding != "identity" else 0)
        length = response.headers.get("content-length")
        if length and length.isdecimal() and int(length) > wire_limit:
            raise ValueError("The model response is too large.")
        body, prefix = bytearray(), bytearray()
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        wire_bytes = 0
        for chunk in response.iter_raw():
            wire_bytes += len(chunk)
            if wire_bytes > wire_limit:
                raise ValueError("The model response is too large.")
            if encoding == "deflate" and decoder is None:
                prefix.extend(chunk)
                if len(prefix) < 2:
                    continue
                # Some HTTP servers send raw DEFLATE instead of its zlib wrapper.
                wrapped = prefix[0] & 15 == 8 and int.from_bytes(prefix[:2]) % 31 == 0
                decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
                chunk, prefix = bytes(prefix), bytearray()
            try:
                decoded = decoder.decompress(chunk, max_bytes - len(body) + 1) if decoder else chunk
            except zlib.error:
                raise ValueError("Invalid compressed model response.") from None
            if len(body) + len(decoded) > max_bytes:
                raise ValueError("The model response is too large.")
            body.extend(decoded)
            if decoder and decoder.unused_data:
                raise ValueError("Invalid compressed model response.")
        if encoding != "identity" and (decoder is None or not decoder.eof):
            raise ValueError("Incomplete compressed model response.")
        return json.loads(body)
