import subprocess


def main():
    subprocess.run(["black", "."])
    subprocess.run(["flake8", "."])
