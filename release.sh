#!/usr/bin/env bash
# Package the extension and generate the repository index used for drag & drop install + update checks.
#   ./release.sh            -> dist/chainsmith-<version>.zip and dist/index.json
# Then attach BOTH files to the GitHub release v<version>:
#   gh release upload v<version> dist/chainsmith-<version>.zip dist/index.json
# Blender reads https://github.com/<repo>/releases/latest/download/index.json, so the newest
# release must always carry index.json (that's how installed users get update notifications).
set -euo pipefail
cd "$(dirname "$0")"
REPO="hassanziah-byte/chainsmith"
B="${BLENDER_EXE:-blender}"
export BLENDER_USER_RESOURCES="$PWD/.blender_isolated"   # never touch the real Blender profile

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' extension/blender_manifest.toml)
rm -rf dist && mkdir dist
"$B" --factory-startup --command extension build --source-dir extension --output-dir dist </dev/null
"$B" --factory-startup --command extension server-generate --repo-dir=dist </dev/null

# Point archive_url at the versioned release asset (absolute, so GitHub's download redirects can't break it)
python - "$VERSION" "$REPO" <<'EOF'
import json, sys
version, repo = sys.argv[1], sys.argv[2]
p = "dist/index.json"; d = json.load(open(p, encoding="utf-8"))
for e in d["data"]:
    e["archive_url"] = f"https://github.com/{repo}/releases/download/v{version}/{e['archive_url'].lstrip('./')}"
json.dump(d, open(p, "w", encoding="utf-8"), indent=2)
print("index.json ->", [e["archive_url"] for e in d["data"]])
EOF

ENC_REPO=$(python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "https://github.com/$REPO/releases/latest/download/index.json")
MIN=$(sed -n 's/^blender_version_min = "\(.*\)"/\1/p' extension/blender_manifest.toml)
echo
echo "Drag & drop link for README:"
echo "https://github.com/$REPO/releases/download/v$VERSION/chainsmith-$VERSION.zip?repository=$ENC_REPO&blender_version_min=$MIN"
