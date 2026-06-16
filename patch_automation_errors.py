from pathlib import Path
import re

path = Path("app/services/automation_runner.py")

if not path.exists():
    print("automation_runner.py not found")
    raise SystemExit(1)

text = path.read_text(encoding="utf-8")

backup = path.with_suffix(".py.bak_fix_dashboard_status")
backup.write_text(text, encoding="utf-8")
print("Backup created:", backup)

# 1) Fix dashboard error:
# TypeError: get_automation_status() got an unexpected keyword argument 'settings'
text = re.sub(
    r"def\s+get_automation_status\s*\(\s*\)\s*->\s*dict\s*:",
    "def get_automation_status(*args, **kwargs) -> dict:",
    text
)

text = re.sub(
    r"def\s+get_automation_status\s*\(\s*\)\s*:",
    "def get_automation_status(*args, **kwargs):",
    text
)

# 2) Make set_automation_enabled also tolerant if dashboard passes extra args
text = re.sub(
    r"def\s+set_automation_enabled\s*\(\s*enabled\s*:\s*bool\s*\)\s*:",
    "def set_automation_enabled(enabled: bool, *args, **kwargs):",
    text
)

text = re.sub(
    r"def\s+set_automation_enabled\s*\(\s*enabled\s*\)\s*:",
    "def set_automation_enabled(enabled, *args, **kwargs):",
    text
)

# 3) Disable unsupported --all for backtest
text = text.replace(
    '["--all"],\n        timeout=600',
    '["--limit", "20"],\n        timeout=300'
)

text = text.replace(
    '["--all"],\r\n        timeout=600',
    '["--limit", "20"],\r\n        timeout=300'
)

# 4) Make backtest optional in automation
if 'AUTOMATION_RUN_BACKTEST' not in text:
    text = text.replace(
        'def run_backtest_if_due(force: bool = False) -> bool:',
        '''def run_backtest_if_due(force: bool = False) -> bool:
    if not force and not env_bool("AUTOMATION_RUN_BACKTEST", False):
        logger.info("Backtest skipped because AUTOMATION_RUN_BACKTEST=false")
        return True
'''
    )

# 5) Make strategy run less often, because it timed out after 240 seconds
if 'STRATEGY_INTERVAL_SECONDS' not in text:
    text = text.replace(
        'def run_strategy() -> bool:',
        '''def run_strategy() -> bool:
    state = get_automation_status()
    interval = env_int("STRATEGY_INTERVAL_SECONDS", 600, 120)
    last_strategy_ts = state.get("last_strategy_epoch")
    now = time.time()

    if last_strategy_ts:
        try:
            elapsed = now - float(last_strategy_ts)
            if elapsed < interval:
                logger.info("Strategy skipped. Next due in %.0f seconds", interval - elapsed)
                return True
        except Exception:
            pass
'''
    )

    # After successful strategy calls, state update may not exist. Add update before return ok in run_strategy block.
    text = text.replace(
        '''    return ok


def run_opportunities() -> bool:''',
        '''    if ok:
        write_state(last_strategy_epoch=time.time(), last_strategy_at=now_iso())

    return ok


def run_opportunities() -> bool:'''
    )

# 6) Remove strategy --all if present and use safer no-arg fallback
text = text.replace(
    '''    ok = run_python_file(
        "app/services/strategies/cli_v6_egx.py",
        ["--all"],
        timeout=240
    )''',
    '''    ok = run_python_file(
        "app/services/strategies/cli_v6_egx.py",
        [],
        timeout=240
    )'''
)

# Avoid duplicate same call block if exists
text = text.replace(
    '''    if ok:
        return True

    ok = run_python_file(
        "app/services/strategies/cli_v6_egx.py",
        [],
        timeout=240
    )

    if ok:
        write_state(last_strategy_epoch=time.time(), last_strategy_at=now_iso())

    return ok''',
    '''    if ok:
        write_state(last_strategy_epoch=time.time(), last_strategy_at=now_iso())

    return ok'''
)

path.write_text(text, encoding="utf-8")
print("automation_runner.py patched successfully.")
