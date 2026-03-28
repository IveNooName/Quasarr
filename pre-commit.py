import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# --- CONFIGURATION ---
VERSION_FILE = Path("quasarr/providers/version.py")
PYPROJECT_FILE = Path("pyproject.toml")


def run(cmd, check=True, capture=False, text=True):
    """Helper to run shell commands comfortably."""
    print(f"⚙️  Exec: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=text)


def get_env(key, default=None):
    return os.environ.get(key, default)


def git_status_has_changes():
    return bool(run(["git", "status", "--porcelain"], capture=True).stdout.strip())


# --- TASKS ---


def task_format():
    print("\n🔍 --- 1. FORMATTING & SYNTAX CHECK ---")

    # Runs Ruff using the rules defined in pyproject.toml
    result = run(["uv", "run", "ruff", "check", "--fix", "."], check=False)

    if result.returncode != 0:
        print("❌ Critical errors or syntax issues found. Fix them before staging.")
        sys.exit(1)

    # Standard formatting (indentation/spacing)
    run(["uv", "run", "ruff", "format", "."], check=False)

    if git_status_has_changes():
        print("✅ Linting fixes applied and staged.")
        run(["git", "add", "."])
        return True

    print("✨ Code style is already perfect.")
    return False


def task_tests():
    print("\n🧪 --- 2. TESTS ---")

    result = run(
        ["uv", "run", "python", "-m", "unittest", "discover", "-s", "tests"],
        check=False,
    )

    if result.returncode != 0:
        print("❌ Test suite failed. Fix the failures before staging.")
        sys.exit(1)

    print("✅ Test suite passed.")


def task_upgrade_deps():
    print("\n📦 --- 3. DEPENDENCIES ---")
    try:
        with open(PYPROJECT_FILE, "rb") as f:
            pyproj = tomllib.load(f)

        def get_pkg_name(dep_str):
            m = re.match(r"^[a-zA-Z0-9_\-\.]+", dep_str)
            return m.group(0) if m else None

        # Main dependencies
        deps = pyproj.get("project", {}).get("dependencies", [])
        if deps:
            pkgs = [get_pkg_name(d) for d in deps if get_pkg_name(d)]
            if pkgs:
                print(f"⬆️  Upgrading main: {pkgs}")
                run(["uv", "add", "--upgrade"] + pkgs, check=False)

        # Groups
        groups = pyproj.get("dependency-groups", {})
        for group, g_deps in groups.items():
            if g_deps:
                pkgs = [get_pkg_name(d) for d in g_deps if get_pkg_name(d)]
                if pkgs:
                    print(f"🏗️  Upgrading group '{group}': {pkgs}")
                    run(
                        ["uv", "add", "--group", group, "--upgrade"] + pkgs, check=False
                    )

        # Lock file
        print("🔒 Refreshing lockfile...")
        run(["uv", "lock", "--upgrade"], check=False)

    except Exception as e:
        print(f"⚠️  Dependency upgrade failed: {e}")

    if git_status_has_changes():
        print("✅ Dependencies updated.")
        run(["git", "add", "."])
        return True
    return False


def task_version_bump():
    print("\n🏷️  --- 4. VERSION CHECK ---")
    new_v = ""

    def get_ver(content):
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        return m.group(1) if m else None

    def bump(v):
        p = v.split(".")
        while len(p) < 3:
            p.append("0")
        try:
            p[-1] = str(int(p[-1]) + 1)
        except Exception:
            p.append("1")
        return ".".join(p)

    def ver_tuple(v):
        try:
            return tuple(map(int, v.split(".")))
        except Exception:
            return (0, 0, 0)

    try:
        print("🌐 Fetching remote to compare versions...")

        # Skip remote comparison cleanly in local environments without origin/main.
        remote_ref = "origin/main"
        has_origin = (
            subprocess.run(
                ["git", "remote", "get-url", "origin"],
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        if has_origin:
            run(["git", "fetch", "origin", "main"], check=False)
            remote_head = subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                check=False,
                capture_output=True,
                text=True,
            )
            if remote_head.returncode == 0:
                try:
                    base = subprocess.check_output(
                        ["git", "merge-base", "HEAD", remote_ref], text=True
                    ).strip()
                except Exception:
                    base = remote_ref
            else:
                print(
                    "ℹ️  origin/main not available. Skipping remote version comparison."
                )
                base = None
        else:
            print(
                "ℹ️  No 'origin' remote configured. Skipping remote version comparison."
            )
            base = None

        # Read Main Version
        try:
            if base:
                main_v_content = subprocess.check_output(
                    ["git", "show", f"{base}:{VERSION_FILE.as_posix()}"], text=True
                )
                main_v = get_ver(main_v_content)
            else:
                main_v = None
        except Exception:
            main_v = None

        # Read Current Version
        curr_v = get_ver(VERSION_FILE.read_text())

        print(f"📊 Main: {main_v} | Current: {curr_v}")

        if main_v and curr_v and ver_tuple(curr_v) <= ver_tuple(main_v):
            new_v = bump(main_v)
            print(f"🚀 Bumping version to: {new_v}")
            content = VERSION_FILE.read_text().replace(f'"{curr_v}"', f'"{new_v}"')
            VERSION_FILE.write_text(content)

            run(["git", "add", "."])
            return True, new_v

    except Exception as e:
        print(f"⚠️  Version check warning (non-fatal): {e}")

    return False, new_v


def main():
    is_ci = "--ci" in sys.argv
    do_upgrade = "--upgrade" in sys.argv or is_ci

    # Run Tasks
    fixed_format = task_format()
    fixed_deps = False
    if do_upgrade:
        fixed_deps = task_upgrade_deps()
    task_tests()

    fixed_version, new_v = task_version_bump()

    # --- CI Specific Logic ---
    if is_ci and (fixed_format or fixed_deps or fixed_version):
        print("\n📤 --- 5. PUSH & REPORT ---")

        run(["git", "config", "--global", "user.name", "github-actions[bot]"])
        run(
            [
                "git",
                "config",
                "--global",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ]
        )

        parts = []
        if fixed_format:
            parts.append("fixed linting")
        if fixed_deps:
            parts.append("upgraded dependencies")
        if fixed_version:
            parts.append(f"increased version to {new_v}")

        msg_body = (
            ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        )
        msg = f"chore: 🤖 {msg_body}"

        try:
            run(["git", "commit", "-m", msg])
            target_ref = get_env("TARGET_REF")
            print(f"🔄 Rebase and pushing to {target_ref}...")
            run(["git", "pull", "--rebase", "origin", target_ref], check=False)
            run(["git", "push", "origin", f"HEAD:{target_ref}"])

            if "GITHUB_OUTPUT" in os.environ:
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("changes_pushed=true\n")
        except subprocess.CalledProcessError as e:
            print(f"❌ ::error::Failed to push fixes. ({e})")
            sys.exit(1)

        repo = get_env("GITHUB_REPO")
        workflow_name = get_env("WORKFLOW_NAME")
        pr_num = get_env("PR_NUMBER")

        if not pr_num:
            try:
                pr_json = subprocess.check_output(
                    ["gh", "pr", "list", "--head", target_ref, "--json", "number"],
                    text=True,
                )
                prs = json.loads(pr_json)
                if prs:
                    pr_num = str(prs[0]["number"])
            except:
                pass

        if pr_num:
            print(f"💬 Posting status update to PR #{pr_num}...")
            fixes_list = ""
            if fixed_format:
                fixes_list += "- ✅ **Formatted Code**\n"
            if fixed_deps:
                fixes_list += "- ✅ **Upgraded Dependencies**\n"
            if fixed_version:
                fixes_list += f"- ✅ **Bumped Version** ({new_v})\n"

            body = f"### 🤖 Auto-Fix Applied\nI fixed the following issues so we can merge:\n{fixes_list}\n"
            body += f"**Note:** Build is now **GREEN** 🟢. Please run `git pull origin {target_ref}` locally.\n"

            Path("comment.md").write_text(body, encoding="utf-8")
            run(
                ["gh", "pr", "comment", pr_num, "--body-file", "comment.md"],
                check=False,
            )

            if target_ref == "dev":
                actions_url = (
                    f"https://github.com/{repo}/actions?query=branch%3A{target_ref}"
                )
                retrigger_body = f"🚀 **Beta Build Triggered!**\n\n[**👉 View the new run**]({actions_url})"
                Path("retrigger.md").write_text(retrigger_body, encoding="utf-8")
                run(
                    ["gh", "pr", "comment", pr_num, "--body-file", "retrigger.md"],
                    check=False,
                )

        print(f"⚡ Triggering workflow: {workflow_name}...")
        ret = run(
            ["gh", "workflow", "run", workflow_name, "--ref", target_ref], check=False
        )

        if ret.returncode != 0:
            print("⚠️  ::warning::Could not auto-trigger next run.")

        sys.exit(0)

    else:
        print("\n✨ Clean run. No changes needed.")
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("changes_pushed=false\n")


if __name__ == "__main__":
    main()
