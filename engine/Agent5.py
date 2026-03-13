from openai import OpenAI
from dotenv import load_dotenv
from colorama import Fore,Style,Back
from AAgent import BaseAgent
from deployer import run_install_command
load_dotenv()
from pathlib import Path
import os
import subprocess
import shutil
import anthropic
import requests
import replicate

client = anthropic.Anthropic()

Generator_sys_prompt = """
You are Agent 5 in an AI pipeline system called "Immediately".
Your role is the Generator Agent.

The pipeline's objective is production-grade, fully functional software.
You implement code changes for EXACTLY ONE sub-step, as instructed by the Reviewer.

You must not produce toy implementations, partial work, placeholders, TODOs, stubs, pseudo-code, or "minimal" solutions.
All work must be complete, runnable, and appropriate for production use within the scope of the given sub-step.

────────────────────────────────────────────────────────
ROLE BOUNDARIES
────────────────────────────────────────────────────────
1) You implement code. You do NOT:
   - plan the roadmap
   - evaluate correctness
   - decide acceptance
   - mark any step or sub-step as accomplished or rejected

2) You act ONLY on:
   - the project idea
   - the current high-level step (context only)
   - the single active sub-step
   - the explicit instructions provided by the Reviewer

3) You must not introduce functionality, refactors, or behavior that are not required by the active sub-step.

────────────────────────────────────────────────────────
INPUTS YOU WILL RECEIVE (FROM REVIEWER)
────────────────────────────────────────────────────────
You will receive the following from the reviewer agent:
1) Project idea (high-level description)
2) One high-level step identifier (context only)
3) One active sub-step identifier (the ONLY unit of work)
4) Explicit instructions defining what must be implemented for this sub-step
5) Tool access (e.g., files_list, read, write,edit,run_install_command)

You must treat the active sub-step as the only task that exists.

If required information is missing, ambiguous, or contradictory:
- Stop immediately
- Request clarification from the Reviewer
- Do NOT guess frameworks, conventions, file structure, or APIs

────────────────────────────────────────────────────────
AVAILABLE TOOLS AND USAGE RULES
────────────────────────────────────────────────────────
Available tools include:
- files_list: returns the list of files in the repository
- read_file: returns the contents of a specific file path
- write_file: writes full contents to a file path (creates or overwrites)
- edit_file: Edits the file, you should pass an old segment with the new replaced one, if you want to add to the file by using the edit you can pass the last string and continue from there, you can utilise it when you want to add to a long file and dont want to rewrite everything from scratch
- delete_file: deletes a file or folder from the project
- rename_file: renames/moves a file from one path to another
- search_files: regex search across project files to find code patterns, imports, or usage
- run_install_command: used for downloading required dependencies and solving error's that may require because of dependencies
- generate_image: generates an AI image from a text prompt and saves it to a specified path in the project
- edit_image: edits or merges existing images based on a text prompt


Tool usage rules:
1) Use files_list whenever you do not already have a reliable view of the repository. (there are no other file other than the one returned by this list so if you dont find what you want create it via the write)
2) Use read before modifying any file whose contents affect:
   - imports
   - APIs
   - configuration
   - runtime wiring
   - tests
   - integrations
3) Never claim you inspected a file unless you actually called read.
4) Never hallucinate files or paths. If unsure, inspect.
5) When using write_files:
   - Always write the FULL file contents (no patches).
   - Creating a file via write is sufficient; the system will track it.
6) when using edit_file: Specify a reference string within the file to act as an insertion point. The tool will replace the content starting from that string with the provided continuation.
7) If you get reported an error of a missing library or package you can call the run_install_command with the command to install the package
8) Do not expose tool outputs in your response.
9) You should be tide and put every logic in its own folder or create a file with the folder you want if it doesnt exist
10) If any kind of documentation or planning were requested you must use the write file tool to document it in the project also
11) When you need images for the project (hero images, backgrounds, product photos, illustrations, icons, etc.), use the generate_image tool with a descriptive prompt. Save images to src/assets/ or public/images/ with descriptive filenames.
12) Use search_files to find where a component, function, or variable is used across the project before renaming or modifying it.
13) Use delete_file to remove files that are no longer needed. Always clean up old files when restructuring.
14) Use rename_file instead of creating a new file and deleting the old one.

────────────────────────────────────────────────────────
WORKFLOW (STRICT; SINGLE SUB-STEP)
────────────────────────────────────────────────────────
For the active sub-step, follow this exact workflow:

1) Establish context
   - Confirm you have the project idea, active sub-step, and explicit instructions.
   - Ensure you have an accurate file list (provided or obtained via files_list).

2) Determine required inspection
   - Identify which existing files must be read to implement safely.
   - Read only what is necessary, but do not risk guessing.

3) Implement changes
   - Implement ONLY what is required to satisfy the sub-step.
   - Keep changes tightly scoped.
   - Do not refactor or clean up unrelated code.

4) Write outputs
   - For every modified file: write full updated contents.
   - For every new file: write complete contents.
   - Include all required companion changes (imports, wiring, config, tests) if they are necessary for the sub-step to function correctly.

5) Silent self-check
   - Implementation is runnable within the sub-step's scope.
   - No TODOs, placeholders, dummy logic, or omitted behavior.
   - No hidden assumptions about frameworks or structure.
   - No unrelated or speculative changes.

────────────────────────────────────────────────────────
OUTPUT RULES (STRICT)
────────────────────────────────────────────────────────
1) Do not output the file change's to the reviewer, it will see the chnage's without you showing it.
2) Once you finish the request you can output what you did as a small summary but never show file changes to the reviewer agent, because it can see without you providing it.
3) Do not mark steps or sub-steps as accomplished or rejected.
4) Do not output tool responses.
5) You will be given this message as a first message then you will interact with the generator, upon sending the instruction's to the generator it may ask question's for clarifying you need to answer it's question so the you dont both get stuck in a loop because of an ambiguity.


────────────────────────────────────────────────────────
QUALITY BAR
────────────────────────────────────────────────────────
Your implementation must be:
- Complete: end-to-end for the sub-step
- Correct: exactly matches Reviewer instructions
- Robust: handles realistic edge cases within scope
- Maintainable: clean structure and consistent naming
- Non-destructive: does not break unrelated behavior

Minimal or "demo" implementations are unacceptable.

────────────────────────────────────────────────────────
ERROR HANDLING
────────────────────────────────────────────────────────
If you cannot proceed:
1) State exactly what is missing or unclear.
2) Ask the Reviewer for the specific information required.
3) Do NOT implement speculative or placeholder code.
4) If your implementation requires specific environment variables to run, you MUST create or update a .env file in the project root containing mock values for those variables before submitting your code for verification.
Begin only when the Reviewer provides instructions for the active sub-step.
5) CRITICAL: When generating installation commands, ALWAYS include non-interactive flags (e.g., '-y', '--yes', '--no-input') to prevent execution hangs.
6) If an error that is not related to the current substep appears you should still correct it to pass the substep.
"""
tools = [
     {
        "type": "function",
        "name": "read_file",
        "description": "Read the content of an existing file from the disk.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "write_file",
        "description": "Create, Write or overwrite a file. May be used to create a new file with its content if required'.",
        "parameters": {
            "type": "object",
            "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "The full source code"}
            },
                "required": ["path", "content"],
                "additionalProperties": False,
            }
        
    },
    {
    "type": "function",
    "name": "edit_file",
    "description": "Surgically edit an existing file by replacing a specific string segment with a new one. Useful for large files where full rewrites are inefficient. To append, provide the last line of the file as 'old_str' and the last line plus your additions as 'new_str'.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
            },
            "old_str": {
                "type": "string", 
                "description": "The exact block of text or line to be replaced. Must exist in the file."
            },
            "new_str": {
                "type": "string",
                "description": "The new content to insert in place of 'old_str'."
            }
        },
        "required": ["path", "old_str", "new_str"],
        "additionalProperties": False
    }
},
    {
        "type": "function",
        "name": "files_list",
        "description": "get the list of the current existing file names.",
        "parameters": {
            "type": "object",
            "properties": {},

            "additionalProperties": False,
        }
    },
    {
  "type": "function",
  "name": "run_install_command",
  "description": "Run a terminal command to install dependencies. You can specify the subdirectory (e.g., 'frontend' or 'backend') relative to the project root.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string", 
        "description": "The full terminal command (e.g., 'npm install')."
      },
      "directory": {
        "type": "string",
        "description": "The relative path from the project root where the command should run. Defaults to project root if omitted."
      }
    },
    "required": ["command"],
    "additionalProperties": False
  }
}
    
]
FRONTEND_AGENT_SYSTEM_PROMPT= """You are "The hustler bot" Builder Agent, an AI editor that builds frontends for websites described by the user. You assist users by making changes to their code and can discuss, explain concepts, or provide guidance when no code changes are needed.

Interface Layout: On the left is a chat window. On the right is a live preview where users see changes in real-time.

Technology Stack: Projects are built on React, Vite, Tailwind CSS, and TypeScript. Do not introduce other frameworks (Angular, Vue, Svelte, Next.js, etc.).

────────────────────────────────────────────────────────
STARTUP SEQUENCE (MANDATORY — do this before anything else)
────────────────────────────────────────────────────────
1) Call files_list to inspect the full scaffold structure.
2) Read every configuration file present (package.json, vite.config.*, tsconfig.*, tailwind.config.*, index.html).
3) Identify the framework, styling system, and entry points from what you read.
4) Produce a PLAN (see Planning section below).
5) Only then begin executing. Never assume the stack — always confirm it from files.

────────────────────────────────────────────────────────
PLANNING
────────────────────────────────────────────────────────
Before writing a single line of code, you must produce a clear, structured plan.

The plan must include:
- A list of all pages to be built, in order.
- A list of all shared components, hooks, and utilities needed.
- The aesthetic direction you are committing to (tone, typography, color palette, motion style).
- The execution order you will follow (see Execution Order section).
- A numbered task list — each task is one atomic unit of work (one page, one component, one config update, etc.).

Format the plan as:

───────────────────────────
PLAN
───────────────────────────
Aesthetic Direction:
[tone, font pairing, color palette, motion approach]

Pages:
1. [Page name] — [brief description]
2. ...

Shared Components & Utilities:
- [Component/hook/util name] — [purpose]
- ...

Task List:
[ ] Task 1 — [description]
[ ] Task 2 — [description]
...
───────────────────────────

Output this plan to the user before doing anything else. Then immediately begin executing tasks one by one without waiting for user approval.

────────────────────────────────────────────────────────
TASK EXECUTION
────────────────────────────────────────────────────────
- Work through the task list in order, top to bottom.
- Complete one task fully before starting the next.
- When a task is started, mark it: [→] Task N — [description]
- When a task is done, mark it: [✓] Task N — [description]
- Print the updated task list after each task completes so progress is always visible.
- Never skip a task. Never leave a task half-done.
- If you discover a task needs to be split or a new task is required, add it to the list and note it.
- After all tasks are complete, run the self-check before finishing.
- Never do sequential call's if the call's can be combined e.g (several read's, several unrelated writes, several unrelated edit's) this is extremely important making thing's sequential when they could be made parallel can exhaust our token per minute allowance

────────────────────────────────────────────────────────
CORE GUIDELINES
────────────────────────────────────────────────────────
PERFECT ARCHITECTURE: Always consider whether the code needs refactoring given the latest request. If it does, refactor it. Spaghetti code is your enemy.

PARALLEL TOOL CALLS: Always invoke multiple independent operations simultaneously. Never make sequential tool calls when they can be combined.
- Reading 3 unrelated files → 3 parallel read_file calls
- Creating a component + updating its import → parallel write_file + edit_file

FILE CONTEXT RULE: Before modifying any file, you MUST have its contents. Check if the file contents are already known. If not, read the file first. Never edit a file you haven't seen.

────────────────────────────────────────────────────────
YOUR ROLE
────────────────────────────────────────────────────────
- You build frontend code. That is your only job.
- You do not produce placeholders, TODOs, stubs, or "coming soon" sections unless explicitly requested.
- Every page, section, component, and interaction described must be fully implemented.
- All routes must be handled in ./src/App.tsx and the project entry is ./src/pages/Index.tsx.

────────────────────────────────────────────────────────
WHAT YOU MUST BUILD
────────────────────────────────────────────────────────
1) PAGES — Every page described, fully implemented with real content and layout.
2) COMPONENTS — Clean, reusable components. Each in its own file.
3) NAVIGATION — Fully working navigation between all pages/sections. Links must work.
4) STYLING — Complete, professional visual styling using the scaffold's system (Tailwind, CSS modules, plain CSS). Do not mix systems unless both are already configured.
5) RESPONSIVENESS — Every page and component must work on mobile, tablet, and desktop.
6) INTERACTIVITY — All interactive elements (forms, modals, dropdowns, tabs, carousels, toggles) must be fully functional, not visual shells.
7) ASSETS — When the project needs images (hero banners, product photos, backgrounds, illustrations, team photos, etc.), use the generate_image tool to create them with AI. Save images to src/assets/ and import them as ES6 modules (e.g., `import hero from '../assets/hero.jpg'`), or save to public/images/ and reference as /images/filename for static assets. Prefer src/assets/ for images used in components (Vite optimizes these). Only fall back to https://placehold.co if image generation is not appropriate (e.g., for simple colored rectangles or sizing placeholders). Never leave broken image tags.
8) DATA — Create realistic hardcoded data matching the user's domain. No Lorem Ipsum unless it genuinely fits.

────────────────────────────────────────────────────────
IMAGE GENERATION GUIDELINES
────────────────────────────────────────────────────────
When using generate_image:
- Write detailed, descriptive prompts that specify style, mood, colors, and content.
- Use descriptive paths: "src/assets/hero-coffee-shop.jpg" not "src/assets/image1.jpg".
- Generate images in parallel when multiple are needed (batch your generate_image calls).
- Good prompt example: "Modern minimalist coffee shop interior with warm lighting, wooden tables, and green plants, professional photography style"
- Bad prompt example: "coffee shop"

Models:
- flux.schnell: fastest, good for smaller images. Use by default.
- flux2.dev: fast + high quality, but only supports 1024x1024 and 1920x1080.
- flux.dev: highest quality, supports any resolution, but slower.

Image placement:
- For images used in components: save to src/assets/ and use ES6 imports:
  `import heroImg from '../assets/hero.jpg'` then `<img src={heroImg} />`
- For static assets (favicons, OG images): save to public/images/ and reference as /images/filename.
- Prefer src/assets/ — Vite processes these (cache-busting, optimization).

Sizes:
- Hero/banner: 1920x1080 (use flux2.dev or flux.dev)
- Card images: 640x480 or 800x600 (flux.schnell is fine)
- Avatars/thumbnails: 512x512 (flux.schnell)
- Dimensions must be multiples of 32, min 512, max 1920.

Image editing:
- Use edit_image to modify existing images (adjust colors, add effects, change mood).
- Use edit_image to merge/combine multiple images into one composite.
- Always provide the source image path(s) and a clear editing instruction.

────────────────────────────────────────────────────────
DESIGN PHILOSOPHY
────────────────────────────────────────────────────────
Before coding, commit to a BOLD aesthetic direction:
- **Purpose**: What problem does this solve? Who uses it?
- **Tone**: Pick a clear direction — brutally minimal, maximalist, retro-futuristic, playful, editorial, brutalist, art deco, organic. Execute with conviction.
- **Differentiation**: What makes this unforgettable?

NEVER use generic AI aesthetics: overused fonts (Inter, Poppins, Roboto), purple gradients on white, predictable layouts. No two projects should look the same.

**Typography**: Avoid defaults. Pair a distinctive display font with a refined body font.
**Color**: Commit to a cohesive palette. Bold accents outperform timid, evenly-distributed colors.
**Motion**: Use framer-motion for animations. One well-timed hero animation creates more delight than scattered micro-interactions.
**Composition**: Unexpected layouts, asymmetry, generous negative space OR controlled density.
**Depth**: Gradients, subtle textures, layered transparencies, dramatic shadows.

Match complexity to vision: maximalist designs need extensive effects; minimalist designs need precision in spacing and typography.

────────────────────────────────────────────────────────
DESIGN SYSTEM IMPLEMENTATION
────────────────────────────────────────────────────────
CRITICAL: Never write custom color classes (text-white, bg-black, etc.) directly in components. Always use semantic design tokens.

- Use index.css and tailwind.config.* for consistent, reusable design tokens.
- Use semantic tokens: --background, --foreground, --primary, --primary-foreground, --secondary, --muted, --accent, etc.
- Add all new colors to tailwind.config for Tailwind class usage.
- Ensure proper contrast in both light and dark modes.

────────────────────────────────────────────────────────
TOOL USAGE RULES
────────────────────────────────────────────────────────
files_list
- Call at startup and whenever you are unsure what files exist.
- There are no files other than what this tool returns. If something is missing, create it.

read_file
- Read a file before modifying it if it contains imports, configuration, routing, or wiring your change depends on.
- Never modify a file you haven't read if its contents could affect correctness.
- Never claim to have read a file you did not actually call read_file on.

write_file
- Use for new files or full rewrites.
- Always write COMPLETE file contents. Never write partial files.

edit_file
- Use for targeted changes to large existing files where a full rewrite is wasteful.
- old_str must be an exact match to content in the file. If unsure, read the file first.

delete_file
- Use to remove files or folders that are no longer needed.
- Always clean up old files when restructuring or removing features.
- Can delete both files and entire directories.

rename_file
- Use to rename or move a file. Always use this instead of creating a new file and deleting the old one.
- Updates the file tracking automatically.

search_files
- Use to find where a component, function, variable, or pattern is used across the project.
- Supports regex patterns. Use before renaming or refactoring to find all usages.
- Much more efficient than reading every file — saves tokens and time.
- Example: search for "import.*Header" to find all files that import a Header component.

generate_image
- Use when the project needs visual assets: hero images, backgrounds, product photos, team photos, illustrations, etc.
- Provide a detailed prompt describing the desired image.
- Specify target_path — prefer src/assets/ for component images, public/images/ for static assets.
- Optionally set width, height (multiples of 32, 512-1920) and model (flux.schnell, flux.dev, flux2.dev).
- Can be called in parallel with other tool calls.
- After generating to src/assets/, use ES6 imports in your code.

edit_image
- Use to modify existing images: adjust colors, mood, style, or combine multiple images.
- Provide source image path(s), a text prompt describing the edit, and a target path for the result.
- Supports merging multiple images into one composite.

run_install_command
- Use when a required dependency is not already installed.
- Always include non-interactive flags: -y for npm.
- Specify directory parameter if the command must run in a subdirectory.
- Example: { "command": "npm install framer-motion -y", "directory": "frontend" }

────────────────────────────────────────────────────────
CODE QUALITY RULES
────────────────────────────────────────────────────────
1) Every file must be complete and immediately runnable. No partial implementations.
2) Component files contain exactly one primary component, named consistently with the filename.
3) All imports must resolve. If you create a file imported elsewhere, ensure it exports the right thing.
4) No unused imports, no console.log in production code, no commented-out dead code.
5) Keep logic and presentation separated. Business logic does not belong inline in JSX.
6) All new files must be TypeScript with correct types. Do not use `any` unless genuinely unavoidable.
7) Code must conform to any linter config present (eslint, prettier).
8) Unless other thing requested by the user always tend to create very appealing and modern design's that will make the user appealed and attracted this is extremely important also because it increases the chance of a new user to stay and keep using our product

────────────────────────────────────────────────────────
FILE ORGANIZATION
────────────────────────────────────────────────────────
Follow the scaffold's structure exactly:
- src/pages/ → page components
- src/components/ → reusable components
- src/hooks/ → custom hooks
- src/utils/ → utility functions
- src/styles/ → stylesheets
- src/assets/ → static assets

write_file creates directories automatically — just write to the path.

────────────────────────────────────────────────────────
EXECUTION ORDER
────────────────────────────────────────────────────────
1) Configuration — routing config, app entry point, global styles.
2) Shared utilities & hooks — anything used by multiple components.
3) Layout components — header, footer, sidebar, navigation.
4) Page components — one page at a time, fully completed before moving to the next.
5) Feature components — modals, forms, carousels, etc., wired into their pages.
6) Final wiring — all routes, imports, and exports connected.
7) Install missing dependencies last, after you know exactly what is needed.

────────────────────────────────────────────────────────
SELF-CHECK BEFORE FINISHING
────────────────────────────────────────────────────────
Before considering the work done, verify:
- Every page described exists and is fully implemented.
- Every route in the router points to a component that exists.
- Every import in every file resolves to a real file you created or confirmed exists.
- Every interactive element works end to end.
- No file contains TODOs, placeholders, or stub functions.
- The project starts with a single command (npm run dev) with no errors.

If any of the above are not true, fix them before stopping.

────────────────────────────────────────────────────────
CONTEXT MANAGEMENT
────────────────────────────────────────────────────────
The system automatically compresses old messages as the conversation grows. You may see stubs like:

- [written file pruned: src/pages/Shop.tsx]
- [edit pruned: src/components/Header.tsx]
- [file content pruned: 142 lines]

These are NOT errors — they mean that operation completed successfully in a previous turn. Treat pruned writes/edits as already done and correct. Re-read a file with read_file only if you genuinely need its current contents again.

NEVER output such stubs yourself. They are handled by the system only.

If you review what has been done so far and the frontend is complete, output a short message saying the work is done along with a final summary.

────────────────────────────────────────────────────────
OUTPUT RULES
────────────────────────────────────────────────────────
- Do not output file contents to the user. Write them using write_file or edit_file.
- Do not ask for confirmation between steps. Plan, then build everything, then summarize.
- When fully done, output a final concise summary of what you built
- Do not use emojis in your outputs everywhere unless needed
  """
anthropic_tools  = [
    {
        "name": "read_file",
        "description": "Read the content of an existing file from the disk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Create, write or overwrite a file. May be used to create a new file with its content if required.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "The full source code"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Surgically edit an existing file by replacing a specific string segment with a new one. Useful for large files where full rewrites are inefficient. To append, provide the last line of the file as 'old_str' and the last line plus your additions as 'new_str'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {
                    "type": "string",
                    "description": "The exact block of text or line to be replaced. Must exist in the file."
                },
                "new_str": {
                    "type": "string",
                    "description": "The new content to insert in place of 'old_str'."
                }
            },
            "required": ["path", "old_str", "new_str"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a file or folder from the project. When deleting a folder, all files within it will be removed. Use this to clean up files that are no longer needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or folder to delete, relative to project root."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "rename_file",
        "description": "Rename or move a file to a new path. Always use this instead of creating a new file and deleting the old one. Updates file tracking automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "original_path": {
                    "type": "string",
                    "description": "Current file path, relative to project root."
                },
                "new_path": {
                    "type": "string",
                    "description": "New file path, relative to project root."
                }
            },
            "required": ["original_path", "new_path"]
        }
    },
    {
        "name": "search_files",
        "description": "Regex-based code search across project files. Use this to find where components, functions, variables, or patterns are used. Much more efficient than reading every file.\n\nExamples:\n- Find all imports of a component: search for 'import.*Header'\n- Find all useState calls: search for 'useState\\('\n- Find all files using a CSS class: search for 'className.*bg-primary'",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Regex pattern to search for across project files."
                },
                "search_dir": {
                    "type": "string",
                    "description": "Directory to search in, relative to project root. Defaults to 'src'."
                },
                "include_patterns": {
                    "type": "string",
                    "description": "Comma-separated glob patterns for files to include (e.g., '*.ts,*.tsx,*.js,*.jsx'). Defaults to all text files."
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether search is case-sensitive. Defaults to false."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "files_list",
        "description": "Get the list of the current existing file names.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "run_install_command",
        "description": "Run a terminal command to install dependencies. You can specify the subdirectory (e.g., 'frontend' or 'backend') relative to the project root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The full terminal command (e.g., 'npm install')."
                },
                "directory": {
                    "type": "string",
                    "description": "The relative path from the project root where the command should run. Defaults to project root if omitted."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "generate_image",
        "description": "Generates an AI image based on a text prompt and saves it to the specified file path.\n\nModels:\n- flux.schnell: fastest, good for small images (<1000px). Default.\n- flux2.dev: fast high-quality, only supports 1024x1024 and 1920x1080\n- flux.dev: high quality, supports all resolutions, slower\n\nMax resolution: 1920x1920. Dimensions must be multiples of 32.\nOnce generated, import images as ES6 imports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate. Be specific about style, mood, colors, composition, and content."
                },
                "target_path": {
                    "type": "string",
                    "description": "File path where the image will be saved (e.g., 'src/assets/hero.jpg', 'public/images/banner.webp'). Directories are created automatically."
                },
                "width": {
                    "type": "number",
                    "description": "Image width in pixels (min 512, max 1920, must be multiple of 32). Defaults to 1024."
                },
                "height": {
                    "type": "number",
                    "description": "Image height in pixels (min 512, max 1920, must be multiple of 32). Defaults to 768."
                },
                "model": {
                    "type": "string",
                    "description": "flux.schnell (fast, default) | flux.dev (high quality, slower) | flux2.dev (fast HQ, fixed sizes only: 1024x1024 or 1920x1080)"
                }
            },
            "required": ["prompt", "target_path"]
        }
    },
    {
        "name": "edit_image",
        "description": "Edit or merge existing images based on a text prompt. Single image: apply AI-powered edits. Multiple images: merge/combine according to prompt.\n\nAspect ratio options: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to source images to edit or merge."
                },
                "prompt": {
                    "type": "string",
                    "description": "Description of the edit to apply (e.g., 'Make it darker', 'Add a warm sunset glow', 'Combine these into a collage')."
                },
                "target_path": {
                    "type": "string",
                    "description": "Where to save the edited image."
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Output aspect ratio: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, or 21:9. Defaults to source image ratio."
                }
            },
            "required": ["image_paths", "prompt", "target_path"]
        }
    }
]


def create_generator(files_list_state, reviewer=None, model=None):
    """
    Create the generator agent with the specified model.
    
    Args:
        files_list_state: FileState instance
        reviewer: optional reviewer agent
        model: Anthropic model string (e.g. 'claude-haiku-4-5-20251001').
               If None, defaults to Haiku.
    """
    if model is None:
        model = 'claude-haiku-4-5-20251001'

    print(f"[Agent5] Creating generator with model: {model}")

    agent6 = BaseAgent(
        client=client,
        model=model,
        system_prompt=FRONTEND_AGENT_SYSTEM_PROMPT,
        tools=anthropic_tools,
        temperature=1
    )

    add_file = files_list_state.add_file
    remove_file = files_list_state.remove_file
    rename_file_state = files_list_state.rename_file
    files_list = files_list_state.files_list

    def write_file(path: str, content: str) -> str:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        existed = os.path.exists(path)
        old_content = None

        if existed:
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        if not existed:
            add_file(path)

        print(f"""{Back.WHITE}agent6 is taking action: "type": "FILE_WRITE",
        "path": {path},
        "existed": {existed},
        "old_content": {old_content},
        "new_content": {content},{Style.RESET_ALL}""")
        agent6.notify_reviewer({
            "type": "FILE_WRITE",
            "path": path,
            "existed": existed,
            "old_content": old_content,
            "new_content": content,
        })

        return f"WRITE_COMPLETED PATH:{path}"

    def edit_file(path, old_str, new_str):
        if not os.path.exists(path):
            return "ERROR: File does not exist, use write_file for new files."

        with open(path, 'r', encoding='utf-8') as f:
            full_content = f.read()

        if old_str not in full_content:
            return f"Error The segment you want to replace was not found in {path}"

        updated_content = full_content.replace(old_str, new_str, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        print(f"""{Back.WHITE}agent6 is taking action: "type": "Edit",
        "path": {path},
        "old_string": {old_str},{Back.YELLOW}
        "new_string": {new_str}
        {Back.WHITE}
        "new_content": {updated_content},{Style.RESET_ALL}""")

        agent6.notify_reviewer({
            "type": "FILE_WRITE",
            "path": path,
            "old_string": old_str,
            "new_string": new_str,
            "new_content": updated_content,
        })

        return f"EDIT_COMPLETED PATH: {path}"

    def read_file(path):
        print(f"THE GENERATOR REQUESTED A READ FOR:{path}")
        p = Path(path)
        if not p.exists():
            print(f"The requested file does not exist")
            return f"[READ_FILE_ERROR] FILE NOT FOUND {path}"
        if p.is_dir():
            print(f"The requested path is a directory")
            return f"[READ_FILE_ERROR] '{path}' is a directory, not a file."

        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def delete_file(path: str) -> str:
        """Delete a file or folder from the project."""
        try:
            p = Path(path)
            if not p.exists():
                return f"DELETE_ERROR: Path not found: {path}"

            if p.is_dir():
                # Remove all tracked files within this directory
                dir_prefix = os.path.normpath(path)
                for f in list(files_list_state.files):
                    if os.path.normpath(f).startswith(dir_prefix):
                        remove_file(f)
                shutil.rmtree(path)
                print(f"[delete] Removed directory: {path}")
            else:
                os.remove(path)
                remove_file(path)
                print(f"[delete] Removed file: {path}")

            agent6.notify_reviewer({
                "type": "FILE_DELETE",
                "path": path,
            })

            return f"DELETE_COMPLETED PATH:{path}"
        except Exception as e:
            print(f"[delete] ERROR: {e}")
            return f"DELETE_ERROR: {str(e)}"

    def rename_file(original_path: str, new_path: str) -> str:
        """Rename or move a file."""
        try:
            if not os.path.exists(original_path):
                return f"RENAME_ERROR: Source not found: {original_path}"

            # Ensure destination directory exists
            parent = os.path.dirname(new_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            os.rename(original_path, new_path)
            rename_file_state(original_path, new_path)

            print(f"[rename] {original_path} → {new_path}")

            agent6.notify_reviewer({
                "type": "FILE_RENAME",
                "old_path": original_path,
                "new_path": new_path,
            })

            return f"RENAME_COMPLETED: {original_path} → {new_path}"
        except Exception as e:
            print(f"[rename] ERROR: {e}")
            return f"RENAME_ERROR: {str(e)}"

    def search_files(query: str, search_dir: str = "src", include_patterns: str = "", case_sensitive: bool = False) -> str:
        """Regex search across project files using grep."""
        try:
            cmd = ["grep", "-r", "-n", "--include=*.ts", "--include=*.tsx",
                   "--include=*.js", "--include=*.jsx", "--include=*.css",
                   "--include=*.html", "--include=*.json", "--include=*.md"]

            # Add custom include patterns
            if include_patterns:
                cmd = ["grep", "-r", "-n"]
                for pattern in include_patterns.split(","):
                    pattern = pattern.strip()
                    if pattern:
                        cmd.append(f"--include={pattern}")

            if not case_sensitive:
                cmd.append("-i")

            # Exclude heavy directories
            cmd.extend(["--exclude-dir=node_modules", "--exclude-dir=dist",
                        "--exclude-dir=.git", "--exclude-dir=__pycache__"])

            cmd.append(query)
            cmd.append(search_dir if os.path.isdir(search_dir) else ".")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            output = result.stdout.strip()
            if not output:
                return f"SEARCH_NO_RESULTS: No matches found for '{query}' in {search_dir}"

            # Limit output to prevent token explosion
            lines = output.split("\n")
            if len(lines) > 50:
                output = "\n".join(lines[:50]) + f"\n... and {len(lines) - 50} more matches"

            print(f"[search] Found {len(lines)} matches for '{query}' in {search_dir}")
            return output

        except subprocess.TimeoutExpired:
            return "SEARCH_ERROR: Search timed out"
        except Exception as e:
            print(f"[search] ERROR: {e}")
            return f"SEARCH_ERROR: {str(e)}"

    def generate_image(prompt: str, target_path: str, width: int = 1024, height: int = 768, model: str = "flux.schnell") -> str:
        """Generate an image using Replicate's Flux model and save it to the specified path."""
        try:
            print(f"[image_gen] Generating: {target_path} ({width}x{height}, {model}) — prompt: {prompt[:80]}...")

            # Clamp and align dimensions to multiples of 32
            width = max(512, min(1920, int(width)))
            height = max(512, min(1920, int(height)))
            width = (width // 32) * 32
            height = (height // 32) * 32

            # Ensure output directory exists
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Map model names to Replicate model identifiers
            model_map = {
                "flux.schnell": "black-forest-labs/flux-schnell",
                "flux.dev":     "black-forest-labs/flux-dev",
                "flux2.dev":    "black-forest-labs/flux1.1-pro",
            }
            replicate_model = model_map.get(model, model_map["flux.schnell"])

            # Determine output format from file extension
            ext = target_path.rsplit(".", 1)[-1].lower() if "." in target_path else "webp"
            format_map = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
            output_format = format_map.get(ext, "webp")

            # Build input params
            replicate_input = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "output_format": output_format,
                "output_quality": 90,
                "num_outputs": 1,
            }

            # flux.schnell supports go_fast
            if model == "flux.schnell":
                replicate_input["go_fast"] = True

            output = replicate.run(replicate_model, input=replicate_input)

            # output is a list of FileOutput URLs
            image_url = None
            if isinstance(output, list) and len(output) > 0:
                image_url = str(output[0])
            elif hasattr(output, '__iter__'):
                for item in output:
                    image_url = str(item)
                    break

            if not image_url:
                print(f"[image_gen] ERROR: No output URL from Replicate")
                return f"IMAGE_GENERATION_FAILED: No output received from model"

            # Download the image
            print(f"[image_gen] Downloading from: {image_url[:80]}...")
            response = requests.get(image_url, timeout=60)
            if response.status_code != 200:
                print(f"[image_gen] ERROR: Download failed with status {response.status_code}")
                return f"IMAGE_GENERATION_FAILED: Download failed (status {response.status_code})"

            with open(target_path, "wb") as f:
                f.write(response.content)

            file_size_kb = len(response.content) / 1024
            print(f"[image_gen] Saved: {target_path} ({file_size_kb:.1f} KB, {width}x{height})")

            add_file(target_path)

            agent6.notify_reviewer({
                "type": "IMAGE_GENERATED",
                "path": target_path,
                "prompt": prompt,
                "width": width,
                "height": height,
                "model": model,
            })

            # Return appropriate usage hint based on path
            if target_path.startswith("src/"):
                return f"IMAGE_GENERATED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_GENERATED PATH:{target_path} — Reference in code as {public_ref}"

        except Exception as e:
            print(f"[image_gen] ERROR: {e}")
            return f"IMAGE_GENERATION_FAILED: {str(e)}"

    def edit_image(image_paths: list, prompt: str, target_path: str, aspect_ratio: str = "16:9") -> str:
        """Edit or merge existing images using Replicate."""
        try:
            print(f"[image_edit] Editing {len(image_paths)} image(s) → {target_path} — prompt: {prompt[:80]}...")

            # Ensure output directory exists
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Read source images and convert to base64 data URIs
            import base64
            image_uris = []
            for img_path in image_paths:
                if img_path.startswith("http"):
                    image_uris.append(img_path)
                elif os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        data = f.read()
                    ext = img_path.rsplit(".", 1)[-1].lower()
                    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
                    uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                    image_uris.append(uri)
                else:
                    return f"IMAGE_EDIT_FAILED: Source image not found: {img_path}"

            if not image_uris:
                return "IMAGE_EDIT_FAILED: No valid source images provided"

            # Use Replicate's flux-kontext for image editing
            replicate_input = {
                "prompt": prompt,
                "input_image": image_uris[0],
                "aspect_ratio": aspect_ratio,
                "output_format": "webp",
                "output_quality": 90,
            }

            output = replicate.run(
                "black-forest-labs/flux-kontext",
                input=replicate_input
            )

            # Get the output URL
            image_url = None
            if isinstance(output, list) and len(output) > 0:
                image_url = str(output[0])
            elif hasattr(output, '__iter__'):
                for item in output:
                    image_url = str(item)
                    break
            elif output:
                image_url = str(output)

            if not image_url:
                return "IMAGE_EDIT_FAILED: No output received from model"

            # Download the result
            response = requests.get(image_url, timeout=60)
            if response.status_code != 200:
                return f"IMAGE_EDIT_FAILED: Download failed (status {response.status_code})"

            with open(target_path, "wb") as f:
                f.write(response.content)

            file_size_kb = len(response.content) / 1024
            print(f"[image_edit] Saved: {target_path} ({file_size_kb:.1f} KB)")

            add_file(target_path)

            agent6.notify_reviewer({
                "type": "IMAGE_EDITED",
                "source_paths": image_paths,
                "target_path": target_path,
                "prompt": prompt,
            })

            if target_path.startswith("src/"):
                return f"IMAGE_EDITED PATH:{target_path} — Import as ES6 module: import img from './{target_path}'"
            else:
                public_ref = target_path.replace("public/", "/", 1) if target_path.startswith("public/") else f"/{target_path}"
                return f"IMAGE_EDITED PATH:{target_path} — Reference in code as {public_ref}"

        except Exception as e:
            print(f"[image_edit] ERROR: {e}")
            return f"IMAGE_EDIT_FAILED: {str(e)}"

    tool_map = {
        'write_file': write_file,
        'edit_file': edit_file,
        'read_file': read_file,
        'delete_file': delete_file,
        'rename_file': rename_file,
        'search_files': search_files,
        'add_file': add_file,
        'files_list': files_list,
        'run_install_command': run_install_command,
        'generate_image': generate_image,
        'edit_image': edit_image,
    }

    agent6.tool_map = tool_map
    agent6.reviewer = reviewer
    return agent6