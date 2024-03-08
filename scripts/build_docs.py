import os
import shutil
import subprocess


def main():
    # Define the build directory (this is the default for Sphinx)
    build_dir = "docs/_build/html"

    # Check if the build directory exists and remove it
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)

    # Now, run the Sphinx build command
    subprocess.run(["sphinx-build", "-b", "html", "docs/", build_dir])


# This allows the script to be run from the command line
if __name__ == "__main__":
    main()
