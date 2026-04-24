#!/usr/bin/env bash
# update-skills.sh - Sync pr-feedback-skills to all agent skill directories
# Usage: ./update-skills.sh [--dry-run]

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS=("fetch-pr-unresolved-feedback" "resolve-pr-feedback")

# Configurable target directories via environment variables.
# Override with: PR_FEEDBACK_TARGETS="/path/a:/path/b" ./update-skills.sh
_default_targets=(
    "$HOME/.claude/skills"
    "$HOME/.codex/skills"
    "$HOME/forge/skills"
)
if [[ -n "${PR_FEEDBACK_TARGETS:-}" ]]; then
    IFS=: read -ra TARGETS <<< "$PR_FEEDBACK_TARGETS"
else
    TARGETS=("${_default_targets[@]}")
fi

DRY_RUN=false

usage() {
    echo "Usage: ./update-skills.sh [--dry-run]"
}

while (($# > 0)); do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if $DRY_RUN; then
    echo "[DRY RUN] No changes will be made."
    echo
fi

RSYNC_OPTS=(
    -av
    --delete
    --exclude='__pycache__'
    --exclude='*.pyc'
)

updated=0
skipped=0
failed=0

for target_base in "${TARGETS[@]}"; do
    if [[ ! -d "$target_base" ]]; then
        echo "[WARN] Target directory does not exist: $target_base"
        ((skipped++)) || true
        continue
    fi

    for skill in "${SKILLS[@]}"; do
        src="$SOURCE_DIR/$skill"
        dst="$target_base/$skill"

        if [[ ! -d "$src" ]]; then
            echo "[ERROR] Source skill not found: $src"
            ((failed++)) || true
            continue
        fi

        echo "Syncing: $skill -> $dst"

        if $DRY_RUN; then
            if rsync "${RSYNC_OPTS[@]}" --dry-run "$src/" "$dst/"; then
                :
            else
                echo "[FAIL] $skill -> $dst"
                ((failed++)) || true
            fi
            echo
        else
            if rsync "${RSYNC_OPTS[@]}" "$src/" "$dst/"; then
                echo "[OK] $skill -> $dst"
                ((updated++)) || true
            else
                echo "[FAIL] $skill -> $dst"
                ((failed++)) || true
            fi
        fi
        echo
    done
done

if ! $DRY_RUN || ((failed > 0)); then
    echo "========================================="
    echo "Updated: $updated  Failed: $failed  Skipped: $skipped"
    echo "========================================="
fi

if ((failed > 0)); then
    exit 1
fi
