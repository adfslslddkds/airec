"""PyInstaller entry point; re-exec the one-file binary in worker mode."""
import multiprocessing
import os
import sys


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--airec-worker":
        os.environ["AIREC_WORKER"] = "1"
        os.environ["AIREC_PROJECT_ROOT"] = os.getcwd()
        from airec.worker import main as worker_main
        worker_main(sys.argv[2:])
    else:
        from airec.__main__ import main as app_main
        app_main()


if __name__ == "__main__":
    main()
