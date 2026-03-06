import os
from colorama import Style, Fore

class FileState:
    def __init__(self, from_scratch=True):
        self.files = []

        if not from_scratch:
            # Try reading the tracked list first
            if os.path.exists("Files_list.txt"):
                with open("Files_list.txt", "r", encoding="utf-8") as f:
                    self.files = [line.strip() for line in f if line.strip()]

            # If the list is empty or missing, scan the workspace as fallback
            # This handles old projects or cases where Files_list.txt was lost
            if not self.files:
                print(f"{Fore.YELLOW}Files_list.txt empty or missing — scanning workspace...{Style.RESET_ALL}")
                skip_dirs  = {"node_modules", "dist", ".git", "__pycache__"}
                skip_files = {"state.json", "messages.jsonl", "meta.json",
                              "prompt.txt", "preview_port.txt", "deduct_credits.json",
                              "Files_list.txt"}
                for root, dirs, filenames in os.walk("."):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fname in filenames:
                        if fname in skip_files:
                            continue
                        rel = os.path.relpath(os.path.join(root, fname), ".")
                        self.files.append(rel)
                # Rebuild Files_list.txt from the scan
                self._save()
                print(f"{Fore.YELLOW}Rebuilt Files_list.txt with {len(self.files)} files{Style.RESET_ALL}")

    def add_file(self, path):
        print(f"{Fore.MAGENTA} Adding: {path} to the file list")
        if path not in self.files:
            self.files.append(path)
        self._save()

    def files_list(self):
        print(f"{Fore.LIGHTBLUE_EX}A request for the files list has occurred!{Style.RESET_ALL}")
        return self.files

    def _save(self):
        with open("Files_list.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(self.files))