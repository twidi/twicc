"""Allow running twicc with ``python -m twicc``."""

# The __main__ guard is required because the background compute task uses
# multiprocessing with "spawn" start method. Spawn re-imports the main module
# in the child process — without this guard, the child would start a second
# server instance instead of running the worker function.
if __name__ == "__main__":
    from twicc import main

    main()
