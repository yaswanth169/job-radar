"""Run every test suite. No network required."""
import subprocess, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_experience.py", "test_verdict.py", "test_phenom.py", "test_detail.py", "test_supabase_store.py", "test_telegram_fanout.py", "test_eightfold.py"]

failed = []
for suite in SUITES:
    print(f"\n{'=' * 60}\n{suite}\n{'=' * 60}")
    r = subprocess.run([sys.executable, os.path.join(HERE, suite)])
    if r.returncode:
        failed.append(suite)

print(f"\n{'=' * 60}")
print("ALL SUITES PASS" if not failed else f"FAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)
