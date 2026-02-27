#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLS_ROOT="$REPO_ROOT/tools"

cd "$TOOLS_ROOT"
# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

python3 - <<'PY'
from collections import Counter, defaultdict
import slskd_api

from soulseek_sync.config import load_config

cfg = load_config()
API_KEY = cfg.slskd_api_key
BASE_URL = cfg.slskd_base_url

c = slskd_api.SlskdClient(host=BASE_URL, api_key=API_KEY)
root = c.transfers.get_all_downloads()

items = []
for u in root:
    user = u.get("username")
    for d in (u.get("directories") or []):
        for f in (d.get("files") or []):
            items.append(
                {
                    "user": user,
                    "state": f.get("state") or f.get("stateDescription") or "unknown",
                    "exception": f.get("exception"),
                    "filename": f.get("filename"),
                }
            )

print(f"queue_items: {len(items)}")
by_state = Counter(i["state"] for i in items)
print("by_state:")
for k, v in by_state.most_common():
    print(f"  {k}: {v}")

reasons = Counter((i["exception"] or "").strip() for i in items if i.get("exception"))
if reasons:
    print("top_exceptions:")
    for r, n in reasons.most_common(10):
        print(f"  {n}x {r}")

# show up to 10 queued/active
interesting = [i for i in items if "queued" in i["state"].lower() or "progress" in i["state"].lower() or "inprogress" in i["state"].lower()]
if interesting:
    print("active_or_queued:")
    for i in interesting[:10]:
        print(f"  - {i['user']} | {i['state']} | {i['filename']}")

st = c.server.state()
print(f"server: {st.get('state')} (connected={st.get('isConnected')} loggedIn={st.get('isLoggedIn')})")
PY


