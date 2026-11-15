"""Generate sample.pdf (2 pages, Helvetica text) with stdlib only."""

import sys
from pathlib import Path

PAGES = [
    [
        "Sparse Retrieval for Lab Notebooks",
        "This paper studies retrieval over small research corpora.",
        "The quokka heuristic reranks passages by lexical surprise.",
    ],
    [
        "Results",
        "Hybrid search beats dense-only search on short technical queries.",
        "We conclude that lexical signals remain useful.",
    ],
]


def build() -> bytes:
    objs: list[bytes] = []
    n = len(PAGES)
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, lines in enumerate(PAGES):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        ops = ["BT", "/F1 14 Tf", "72 720 Td", "20 TL"]
        ops += [f"({t}) Tj T*" for t in lines]
        ops.append("ET")
        s = "\n".join(ops).encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(s) + s + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


if __name__ == "__main__":
    Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("sample.pdf")).write_bytes(build())
