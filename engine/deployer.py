import subprocess,os
from colorama import Back,Fore,Style
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from Agent import BaseAgent
import re
load_dotenv()
client = OpenAI()
import json

Architect_sys_prompt = """You are the "Execution Architect". Your role is to design a universal 3-gate verification pipeline for any provided source code.

THE 3-GATE PIPELINE:

Gate 1 (Syntax/Static Analysis): Lightweight validation.
- Rule: Use language-native syntax checks (e.g., `python3 -m py_compile`, `bash -n`) ONLY if the file is standard, interpretable code.
- CRITICAL EXCEPTION: If the code requires transpilation, compilation, or a framework to run (e.g., JSX, TypeScript, C++ source, Sass), SKIP Gate 1 (leave array empty) to avoid false negatives. Defer validation to Gate 2.

Gate 2 (Build/Compile): The transformation phase. Use this for installing dependencies, compiling binaries, transpiling code, or preparing environments.

Gate 3 (Run/Execute): The invocation phase. Execute the resulting binary or entry-point script.

OPERATIONAL RULES:

Directory Isolation (CWD): cwd MUST be the relative path from the project root to the directory containing the file. All commands in the suite will execute within this folder.

Tooling Strategy:
1. There are no other file's other than the name's that will be passed to you if there is.
1. Context-Awareness: Use `read_file` to inspect the code. If you see framework-specific syntax (e.g., React imports, Type annotations, Headers), assume a build step is required.
2. Avoid Global Installs: Strictly avoid `npm install -g`. Use local installs or native runtime flags.
3. Purity: Output ONLY valid JSON. No conversational filler.

The elements that you put inside the arrays should be executable commands.

You can view whichever file there's no specific constraints but you need to check if everything is working in all phases for the project when applicable. If you view enough files and understood what is required for checking then you can stop viewing and output the json.

JSON STRUCTURE:

JSON
{
  "test_suites": [
    {
      "language": "extension_or_name",
      "cwd": "path/to/folder",
      "gate1_syntax": [],
      "gate2_build": [],
      "gate3_run": []
    }
  ]
}
There could be multiple suites if required.

CRITICAL STATE MANAGEMENT:

Strict JSON Re-Output: If you receive an error input, you must resolve it (via run_install_command or logic revision) and immediately output the JSON structure again.

Even if no changes are made to the test suite after a tool call, you MUST re-state the JSON. The pipeline treats the absence of JSON as a termination signal.

Failure to output the JSON after a tool call will break the verification loop.

CRITICAL: When generating installation or build commands, ALWAYS include non-interactive flags (e.g., '-y', '--yes', '--no-input') to prevent execution hangs.
"""

gate_sys_prompt = """

You are a gate agent in a code execution pipeline you will be provided with a test suite and an output error,
Your task is to output either exactly 0 if the error is regarding the test suites and the tools that is mentioned in it so we cant route the error back to the agent that outputted the test suite,
or output exactly 1 if the error is regarding things unrelated with the test tools and therefore you should output exactly 1 so we can route the error back to the code implementation agent.

The purpose of this is to discriminating between the error that might occur because of a missing test suite tool that has been requested by the test suite agent
and therefore route the error back so the test agent can solve the issue either by downloading the missing tool or changing the tool, and the casual coding errors that might occur because of the problems in the implementation or the dependencies or whatever.

Rules:
Rememmber either exactly 0 or 1 nothing else no explanation no talk strictly
binary no third option output is available other than 0 or 1


"""
timeout_gate_prompt = """
    You are a Log Analysis Agent. Your ONLY job is to determine if a timeout stopping was clear of errors.
    You are an agent in a code execution pipeline. You will be given logs after slaughtering a long-staying process.
    
    Your job is to:
    - Output "0" indicating there are no errors (normal timeout slaughtering).
    - Output "1" if there were accompanying errors with the termination.

    INPUT:
    - Stdout/Stderr logs

    RULES:
    1. If the logs show "Compiled with problems", "Failed to compile", "Traceback", or specific "ERROR" messages, output "1".
    2. If the logs show "Compiled successfully", "Server running", "Listening on port", or generic info messages without fatal errors, output "0".
    3. Ignore "warnings" (yellow text) unless they explicitly say they broke the build.

    OUTPUT FORMAT:
    Strictly one digit: "0" or "1".
    No fillers no explanations no nothing just either 0 or 1 without anyother thing strictly.
    
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




def execute_anything(command,cwd="Pipeline",timeout=60):
    
    full_cwd = os.path.abspath(cwd)

    try:
        process = subprocess.run(command,
                                 shell=True,
                                 cwd=full_cwd,
                                 capture_output=True,
                                 text=True,
                                 timeout=timeout
                                 )
        
        return {
            'status': 'COMPLETED',
            'exit_code': process.returncode,
            'stdout': process.stdout,
            'stderr': process.stderr
        }
    except subprocess.TimeoutExpired as e:
        timeoutgate = BaseAgent(client=client,model="gpt-4.1-mini",system_prompt=timeout_gate_prompt)
        stdout = e.stdout.decode() if e.stdout else ""
        stderr=  e.stderr.decode() if e.stderr else ""
        details = {"stdout": stdout if e.stdout else "","stderr":stderr if e.stderr else ""}
        case = timeoutgate.chat(str(details)).strip()
        
        
        print(f"{Back.RED}The timeout gate case is {case} stderr:{stderr} stdout:{stdout}")
        return {
            'status': 'STILL_RUNNING_SLAUGHTERED',
            'exit_code': int(case),
            'stdout': stdout,
            'stderr': stderr
        }
    
    except Exception as e:
        return {
            'status': 'SYSTEM_ERROR',
            'exit_code': 1,
            'stdout': "",
            'stderr': str(e)
        }

def execute_all():
    

    try:
        with open('Files_list.txt','r') as f:
            file_paths = [line.strip() for line in f if line.strip()]

    except FileNotFoundError:
        print(f"{Back.RED}File_list.txt was NOT FOUND. NOTHING TO EXECUTE.{Style.RESET_ALL}")
        return {"success":False,
                "details": f"File: File_list.txt"}

    current_files = '\n'.join(file_paths)
    model = "gpt-4.1-mini"
    def read_file(path):
        
        print(f"THE ARCHITECT REQUESTED A READ FOR:{path}")
        p = Path(path)
        if not p.exists():
            print(f"The requested file does not exist")
            return f"[READ_FILE_ERROR] FILE NOT FOUND {path}"
        with open(path,'r',encoding='utf-8') as f:
            return f.read()
    tool_map = {
        "read_file": read_file,
        "run_install_command": run_install_command
    }

    Agent7 = BaseAgent(client=client,model=model,system_prompt=Architect_sys_prompt,tools=tools,tool_map=tool_map,temperature=0)

    response = Agent7.chat(f"The Current existing project files (This is all what is in the repo dont assume other files's): {current_files}")
    print(f"{Back.RED}Architect Output: {response}{Style.RESET_ALL}")

    retry_attempts =0
    max_retries=5

    while retry_attempts<max_retries:
        state=False
        match = re.search(r'(\{.*\})',response,re.DOTALL)

        if match:
            cleaned_response = match.group(0)
        else:

            cleaned_response = response.strip().replace("```json","").replace("```","").strip()

    
        try:
            plan = json.loads(cleaned_response)
        except Exception as e:
            print(f"{Back.RED}Architect returned invalid json.{Style.RESET_ALL}")
            return {
                'success': False,
                'details': f"Architect returned invalid json {str(e)}"
            }

        print(f"Architect plan:{plan}")
        suites = plan.get('test_suites',[])
        s_idx =0
        state =False

        while s_idx<len(suites):
            suite = suites[s_idx]
            relative_cwd = suite.get('cwd','.').lstrip('/')
            absolute_cwd = os.path.abspath(os.path.join("Pipeline",relative_cwd))

            if not os.path.exists(absolute_cwd):
                print(f"{Back.RED}Directory not found {absolute_cwd}{Style.RESET_ALL}")
                return {
                "success": False,
                "details": f"Directory not found: {absolute_cwd}"
                }
        
        
            gates = [
                ("Syntax", suite.get('gate1_syntax',[])),
                ("Build", suite.get('gate2_build',[])),
                ("Run", suite.get('gate3_run',[]))
            ]
            g_idx = 0
            while g_idx<len(gates):
                gate_name,commands= gates[g_idx]
            
                cmd_idx= 0
                


                while cmd_idx<len(commands):
                    cmd = commands[cmd_idx]
                    print(f"{Back.RED}Running {gate_name} ({suite['language']}): {cmd}{Style.RESET_ALL}")
                    result = execute_anything(cmd,cwd=absolute_cwd)
                    if result['exit_code']!=0:
                        print(f"{Back.RED}Vibe check failed at {gate_name}{Style.RESET_ALL}")
                        gate3 = BaseAgent(client=client,model=model,system_prompt=gate_sys_prompt)
                    
                        full_error = (result['stdout']+"\n"+result['stderr']).strip()
                        answer =  {
                        'success': False,
                        'langauge': suite['language'],
                        'gate': gate_name,
                        'directory':relative_cwd,
                        'cmd':cmd,
                        'details':full_error if full_error else "UNKOWN ERROR, No output were captured",
                        }
                    
                        binary = gate3.chat(str(answer)).strip()
                        print(f"{Back.YELLOW}Gate output: ",binary)
                        print(f"{Back.RED}The Error:{str(answer)}{Style.RESET_ALL}")
                        if "0" in binary:
                            print(f"{Fore.MAGENTA}Routing to architect for environment fix...")
                            response =Agent7.chat(str(answer))
                            print(f"The architect returned this after correction:",response)
                            retry_attempts+=1
                            state =True
                            break
                        else: 
                            return answer
                            

                    cmd_idx+=1
                if state: 
                    break
                g_idx+=1
            if state: 
                break
            s_idx+=1
        if state:
            continue
        print(f"{Back.RED}Vibe check passed for all domains{Style.RESET_ALL}")
        return {
            'success': True,
            'message': f"all domains passed"
        }

    print(f"{Back.RED}Exceeded max attemtps")
    return {
        "success":False,
        "details": "Max retries exceeded"
    }


    



def run_install_command(command,directory= '.'):
    full_path = directory
    print(f"{Back.RED}running a command: {command} in {full_path}{Style.RESET_ALL}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=full_path,
            timeout=60,
            input="y/n"*10,

        )

        if result.returncode==0:
            print(f"{Back.RED}Install success: {result.stdout}{Style.RESET_ALL}")
            return f"INSTALL_SUCCESS: {result.stdout}"
        else:
            print(f"{Back.RED}Install Failure: {result.stderr}{Style.RESET_ALL}")
            return f"INSTALL_FAILURE: stderr:{result.stderr} stdout: {result.stdout}"
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode() if e.stdout else ""
        stderr = e.stderr.decode() if e.stderr else ""
        output_so_far = (stdout) + "\n" + (stderr)
        print(f"{Back.RED}TIMEOUT_ERROR: {output_so_far}{Style.RESET_ALL}")
        return f"TIMEOUT_ERROR: The command hung. Output so far:\n{output_so_far}"





    except Exception as e:
        print(f"{Back.RED}SYSTEM ERROR: {e}{Style.RESET_ALL}")
        return f"SYSTEM_ERROR: {e}"
            




