"""Start the dashboard:   python dashboard.py     (then open http://localhost:8765)"""
import sys

if __name__ == "__main__":
    if "--overlay" in sys.argv:                 # the overlay window that sits on top of the game (started from the dashboard's "Show overlay" button)
        from simtracker.overlay import run
        run()
    else:
        from simtracker.server import main
        main()
