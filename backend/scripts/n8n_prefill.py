"""Write n8n workflow copies with the Config nodes filled in, ready to import.

    python -m scripts.n8n_prefill https://<random>.trycloudflare.com [--email you@gmail.com]

Reads API_KEY and N8N_WEBHOOK_SECRET from backend/.env (or --env-file, e.g. the server's
deploy/api.env) and writes ../n8n/local/*.json (git-ignored: the copies contain secrets).
Run it again whenever the backend URL changes or you move to another n8n account, then
re-import. Prints no secret values.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPIENTS = ("reviewer_email", "demo_inbox", "summary_to")


def env_value(text: str, name: str) -> str:
    m = re.search(rf"^{name}=(.*)$", text, re.M)
    return m.group(1).strip().strip('"') if m else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("backend_url", help="public URL of the backend (the tunnel)")
    parser.add_argument("--email", default="", help="Gmail address for the recipient fields")
    parser.add_argument(
        "--env-file",
        default=str(ROOT / "backend" / ".env"),
        help="where API_KEY and N8N_WEBHOOK_SECRET come from (e.g. the server's deploy/api.env)",
    )
    parser.add_argument("--app-url", default="", help="the site's URL for links in emails")
    parser.add_argument(
        "--intake-to",
        default="",
        help="only take emails sent to this address (Gmail trigger search), e.g. a dedicated inbox",
    )
    args = parser.parse_args()

    env = Path(args.env_file).read_text(encoding="utf-8")
    fill = {
        "backend_url": args.backend_url.rstrip("/"),
        "webhook_secret": env_value(env, "N8N_WEBHOOK_SECRET"),
        "api_key": env_value(env, "API_KEY"),
    }
    optional = {"app_url": args.app_url.rstrip("/")} if args.app_url else {}
    missing = [k for k, v in fill.items() if not v]
    if missing:
        raise SystemExit(f"missing in {args.env_file} or arguments: {', '.join(missing)}")

    out = ROOT / "n8n" / "local"
    out.mkdir(exist_ok=True)
    for src in sorted((ROOT / "n8n" / "workflows").glob("*.json")):
        wf = json.loads(src.read_text(encoding="utf-8"))
        todo: list[str] = []
        for node in wf["nodes"]:
            if node["type"] == "n8n-nodes-base.gmailTrigger" and args.intake_to:
                node["parameters"]["filters"]["q"] = (
                    f"to:{args.intake_to} has:attachment filename:pdf is:unread"
                )
            if node["name"] != "Config":
                continue
            for a in node["parameters"]["assignments"]["assignments"]:
                if a["name"] in fill:
                    a["value"] = fill[a["name"]]
                elif a["name"] in optional:
                    a["value"] = optional[a["name"]]
                elif a["name"] in RECIPIENTS:
                    a["value"] = args.email or "YOUR-GMAIL-ADDRESS"
                    if not args.email:
                        todo.append(a["name"])
        (out / src.name).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"{src.name}: ready" + (f" (fill in: {', '.join(todo)})" if todo else ""))
    print(f"→ import the files in {out}")


if __name__ == "__main__":
    main()
