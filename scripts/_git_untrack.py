import subprocess
import sys

files = ["AI手语翻译平台-技术方案.md", "AI手语识别翻译平台_设计方案与技术路线.md"]
result = subprocess.run(["git", "rm", "--cached", "--"] + files, capture_output=True)
print("stdout:", result.stdout.decode("utf-8", "replace"))
print("stderr:", result.stderr.decode("utf-8", "replace"))
print("exit:", result.returncode)
