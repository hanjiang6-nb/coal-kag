"""Check staged/tracked content before committing or uploading code."""
import pathlib
import subprocess
import sys

LIMIT = 100_000_000
BLOCKED_DIRS = {"data", "datasets", "indexes", "logs", "backups"}
BLOCKED_SUFFIXES = {
    ".csv", ".tsv", ".parquet", ".arrow", ".feather", ".xlsx", ".xls",
    ".db", ".sqlite", ".sqlite3", ".dump",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".log", ".pem", ".key", ".p12",
}


def violation(name, size):
    path = pathlib.PurePosixPath(name)
    if size >= LIMIT:
        return "file must be smaller than 100,000,000 bytes"
    if any(part.lower() in BLOCKED_DIRS for part in path.parts[:-1]):
        return "data/generated directory is forbidden"
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        return "data, archive, or credential file is forbidden"
    if (path.name == ".env" or path.name.startswith(".env.")) and not path.name.endswith(".example"):
        return "local environment file is forbidden"
    return None


def main():
    root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    errors = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        name = raw_name.decode("utf-8", errors="replace")
        if stage != b"0" or mode == b"160000":
            errors.append(name + ": unresolved conflict or unchecked submodule")
            continue
        size = int(subprocess.check_output(["git", "cat-file", "-s", oid.decode()], cwd=root))
        problem = violation(name, size)
        if problem:
            errors.append(name + ": " + problem)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("PASS: all indexed files comply with upload rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
