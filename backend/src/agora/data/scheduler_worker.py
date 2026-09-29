"""Standalone entry point for the durable API dataset scheduler worker."""

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    from agora.data.scheduler import main as run_scheduler

    run_scheduler()


if __name__ == "__main__":
    main()
