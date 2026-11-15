"""Починить конфиг nginx после неудачной вставки заголовков."""

from pathlib import Path

CONF = Path("/etc/nginx/sites-available/football-landing")

HEADERS = [
    '    add_header Strict-Transport-Security "max-age=31536000" always;',
    '    add_header X-Frame-Options "SAMEORIGIN" always;',
    '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
    '    add_header X-Content-Type-Options "nosniff" always;',
]


def main() -> None:
    lines = CONF.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen_headers = False
    for line in lines:
        stripped = line.strip()
        # Строка server_name могла быть испорчена вставкой заголовков.
        if stripped.startswith("server_name"):
            out.append("    server_name footballinho.ru www.footballinho.ru;")
            continue
        if stripped.startswith("add_header Strict-Transport-Security"):
            out.append(HEADERS[0])
            continue
        if stripped.startswith("add_header X-Frame-Options"):
            continue
        if stripped.startswith("add_header Referrer-Policy"):
            continue
        if stripped.startswith("add_header X-Content-Type-Options"):
            continue
        if stripped == "www.footballinho.ru;":
            continue
        out.append(line)

    # Заголовки ставим сразу после server_name
    for index, line in enumerate(out):
        if line.strip().startswith("server_name"):
            out[index + 1:index + 1] = HEADERS
            seen_headers = True
            break

    CONF.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("заголовки на месте:", seen_headers)
    print("\n".join(out[:9]))


if __name__ == "__main__":
    main()
