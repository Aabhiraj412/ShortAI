"""
Targeted Path Migration Script for D:\Agents\CrazzyDocs.
Phase 5, Stage 3.15 Standard.

Updates:
1. Active backend core, services, rendering, scheduler, and content modules to import and use backend.app.core.paths.
2. Active backend tests to use dynamic path resolution.
3. Active scripts in backend/scripts/ to use D:\Agents\CrazzyDocs.
4. Root launch scripts (start_studio.bat, start_studio.ps1, run_pipeline.bat, run_in_env.ps1, env.ps1) to use D:\Agents\CrazzyDocs and .venv.
5. SQLite database publishing_ledger records to update D:\AI Arts\AI -> D:\Agents\CrazzyDocs.
"""

import os
import sqlite3
import re

DEST_ROOT = r"D:\Agents\CrazzyDocs"

def replace_in_file(rel_path, replacements):
    abs_path = os.path.join(DEST_ROOT, rel_path)
    if not os.path.isfile(abs_path):
        print(f"[SKIP MISSING] {rel_path}")
        return False
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()

    modified = content
    for old_txt, new_txt in replacements:
        if old_txt in modified:
            modified = modified.replace(old_txt, new_txt)
        else:
            print(f"[WARN] Target not found in {rel_path}: {old_txt[:60]}...")

    if modified != content:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(modified)
        print(f"[UPDATED] {rel_path}")
        return True
    else:
        print(f"[NO CHANGE] {rel_path}")
        return False

def migrate_database():
    db_path = os.path.join(DEST_ROOT, "backend", "studio.db")
    print(f"\nMigrating database paths in {db_path}...")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT id, video_path, thumbnail_path FROM publishing_ledger WHERE video_path LIKE '%AI Arts%' OR thumbnail_path LIKE '%AI Arts%'")
    rows = cur.fetchall()
    print(f"Found {len(rows)} publishing_ledger rows with old path.")
    for row_id, v_path, t_path in rows:
        new_v = v_path.replace(r"D:\AI Arts\AI", DEST_ROOT).replace("D:/AI Arts/AI", DEST_ROOT) if v_path else v_path
        new_t = t_path.replace(r"D:\AI Arts\AI", DEST_ROOT).replace("D:/AI Arts/AI", DEST_ROOT) if t_path else t_path
        cur.execute("UPDATE publishing_ledger SET video_path = ?, thumbnail_path = ? WHERE id = ?", (new_v, new_t, row_id))
    con.commit()
    con.close()
    print("Database publishing_ledger paths successfully updated.")

def main():
    print("=" * 80)
    print("APPLYING CENTRALIZED PATH RESOLUTION TO D:\\Agents\\CrazzyDocs")
    print("=" * 80)

    # 1. Database
    migrate_database()

    # 2. Backend core modules
    replace_in_file(
        os.path.join("backend", "app", "database", "db.py"),
        [('BASE_DIR = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as BASE_DIR')]
    )

    replace_in_file(
        os.path.join("backend", "app", "branding", "outro_generator.py"),
        [('WORKSPACE = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE')]
    )

    replace_in_file(
        os.path.join("backend", "app", "content", "content_integrity_gate.py"),
        [('WORKSPACE = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE')]
    )

    replace_in_file(
        os.path.join("backend", "app", "content", "topic_ledger.py"),
        [
            ('DB_PATH = r"D:\AI Arts\AI\backend\studio.db"', 'from backend.app.core.paths import DATABASE_PATH, BACKEND_DIR\nDB_PATH = str(DATABASE_PATH)'),
            ('HISTORY_JSON_PATH = r"D:\AI Arts\AI\backend\app\content\topic_history.json"', 'HISTORY_JSON_PATH = str(BACKEND_DIR / "app" / "content" / "topic_history.json")')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "orchestration", "crazzydocs_master_orchestrator.py"),
        [('WORKSPACE = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE')]
    )

    replace_in_file(
        os.path.join("backend", "app", "orchestration", "production_workspace.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engine.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "compositing", "compositor.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, FFPROBE_DEFAULT_PATH as _FPR\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('FFPROBE_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffprobe.exe"', 'FFPROBE_DEFAULT_PATH = str(_FPR)')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "cinematic_thumbnail_designer.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "clean_slate_renderer.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, PROJECT_ROOT_STR as WORKSPACE_ROOT\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('WORKSPACE_ROOT = r"D:\AI Arts\AI"', '# Replaced above')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "creative_thumbnail_engine.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "thumbnail_generator.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "vesna_fast_renderer.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, PROJECT_ROOT_STR as WORKSPACE_ROOT\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('WORKSPACE_ROOT = r"D:\AI Arts\AI"', '# Replaced above')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "engines", "yamaguchi_fast_renderer.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, PROJECT_ROOT_STR as WORKSPACE_ROOT\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('WORKSPACE_ROOT = r"D:\AI Arts\AI"', '# Replaced above')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "quality", "frame_validator.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, FFPROBE_DEFAULT_PATH as _FPR\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('FFPROBE_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffprobe.exe"', 'FFPROBE_DEFAULT_PATH = str(_FPR)')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "rendering", "quality", "quality_gate.py"),
        [
            ('FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"', 'from backend.app.core.paths import FFMPEG_DEFAULT_PATH as _FFP, FFPROBE_DEFAULT_PATH as _FPR\nFFMPEG_DEFAULT_PATH = str(_FFP)'),
            ('FFPROBE_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffprobe.exe"', 'FFPROBE_DEFAULT_PATH = str(_FPR)')
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "scheduler", "reconciliation_service.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "services", "email_monitoring_service.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "services", "expressive_tts_engine.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "services", "media_lifecycle_manager.py"),
        [
            ('WORKSPACE = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE'),
            (r'D:\AI Arts\AI', DEST_ROOT)
        ]
    )

    replace_in_file(
        os.path.join("backend", "app", "services", "notification_service.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    replace_in_file(
        os.path.join("backend", "app", "services", "youtube_publishing_engine.py"),
        [('WORKSPACE_ROOT = r"D:\AI Arts\AI"', 'from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT')]
    )

    # 3. Scripts
    replace_in_file(
        os.path.join("backend", "scripts", "configure_youtube_oauth.py"),
        [(r'WORKSPACE_ROOT = r"D:\AI Arts\AI"', f'WORKSPACE_ROOT = r"{DEST_ROOT}"')]
    )

    replace_in_file(
        os.path.join("backend", "scripts", "manage_windows_startup.py"),
        [(r'WORKSPACE_ROOT = r"D:\AI Arts\AI"', f'WORKSPACE_ROOT = r"{DEST_ROOT}"')]
    )

    replace_in_file(
        os.path.join("backend", "scripts", "verify_phase_10.py"),
        [
            ('sqlite3.connect("backend/studio.db")', f'sqlite3.connect(r"{DEST_ROOT}\\backend\\studio.db")'),
            ('"Youtube Channel Src/Youtube-Icon.png"', f'r"{DEST_ROOT}\\Youtube Channel Src\\Youtube-Icon.png"'),
            ('"media/source/branding/crazzydocs_outro_1080x1920.mp4"', f'r"{DEST_ROOT}\\media\\source\\branding\\crazzydocs_outro_1080x1920.mp4"'),
            ('"backend/token.json"', f'r"{DEST_ROOT}\\backend\\token.json"'),
            ('"backend/client_secrets.json"', f'r"{DEST_ROOT}\\backend\\client_secrets.json"')
        ]
    )

    replace_in_file(
        os.path.join("backend", "scripts", "verify_email_dashboard.py"),
        [(r'WORKSPACE_ROOT = r"D:\AI Arts\AI"', f'WORKSPACE_ROOT = r"{DEST_ROOT}"')]
    )

    replace_in_file(
        os.path.join("backend", "scripts", "verify_mobile_email_layout.py"),
        [(r'WORKSPACE_ROOT = r"D:\AI Arts\AI"', f'WORKSPACE_ROOT = r"{DEST_ROOT}"')]
    )

    # 4. Tests
    test_files_to_update = [
        "test_branding_integrity.py",
        "test_production_workspace.py",
        "test_stage_3_13b_lifecycle_audit.py",
        "test_stage_3_9b_media_lifecycle.py",
        "test_stage_3_13a_content_integrity.py",
        "test_stage_3_12_youtube.py",
        "test_stage_3_9cd_scheduler.py",
        "test_stage_3_9f_email_dashboard.py",
        "test_stage_3_10_resilience.py",
    ]

    for tf in test_files_to_update:
        rel_tf = os.path.join("backend", "tests", tf)
        replace_in_file(
            rel_tf,
            [
                (r'WORKSPACE = r"D:\AI Arts\AI"', f'WORKSPACE = r"{DEST_ROOT}"'),
                (r'WORKSPACE_ROOT = r"D:\AI Arts\AI"', f'WORKSPACE_ROOT = r"{DEST_ROOT}"'),
                (r'D:\AI Arts\AI', DEST_ROOT)
            ]
        )

    # 5. Root Launch Scripts
    replace_in_file(
        "start_studio.bat",
        [
            ("set BASEDIR=D:\\AI Arts\\AI", f"set BASEDIR={DEST_ROOT}"),
            ("python-env", ".venv")
        ]
    )

    replace_in_file(
        "start_studio.ps1",
        [
            ('$baseDir = "D:\\AI Arts\\AI"', f'$baseDir = "{DEST_ROOT}"'),
            ("python-env", ".venv")
        ]
    )

    replace_in_file(
        "run_pipeline.bat",
        [
            ("set BASEDIR=D:\\AI Arts\\AI", f"set BASEDIR={DEST_ROOT}"),
            ("python-env", ".venv")
        ]
    )

    replace_in_file(
        "run_in_env.ps1",
        [
            ('$baseDir = "D:\\AI Arts\\AI"', f'$baseDir = "{DEST_ROOT}"'),
            ("python-env", ".venv")
        ]
    )

    replace_in_file(
        "env.ps1",
        [
            ('$baseDir = "D:\\AI Arts\\AI"', f'$baseDir = "{DEST_ROOT}"'),
            ("python-env", ".venv")
        ]
    )

    print("\nCentralized path migration completed successfully.")

if __name__ == "__main__":
    main()
