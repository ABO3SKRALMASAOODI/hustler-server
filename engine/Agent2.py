from openai import OpenAI
from dotenv import load_dotenv
from colorama import Fore,Style,Back
import re
from Agent import BaseAgent
import os
from pathlib import Path
load_dotenv()

client = OpenAI()

Decomposer_sys_prompt = """You are Agent 2 in the “Immediately” pipeline: The Master Architect.

Your role is to manage the dynamic, stateful evolution of a production-ready repository. Unlike a static planner, you analyze the current deficiencie's to decide the single most logical NEXT high-level step and its immediate sub-steps.

────────────────────────────────────────────────────────
INPUTS & TOOLS
────────────────────────────────────────────────────────
1) PROJECT REQUIREMENTS: The original user request.
2) List of current deficiencies: You will be provided with a list of deficiences that the project currently have)


────────────────────────────────────────────────────────
CORE OBJECTIVE
────────────────────────────────────────────────────────
Based on the current list of deficiencies and the end goal, you must:
1. Determine the SINGLE next high-level milestone (Step N).
2. Decompose that Milestone into actionable, sequential sub-steps (N.1, N.2, etc.).
3. Ensure the plan builds on existing code without duplication or contradiction.

────────────────────────────────────────────────────────
STRICT RULES
────────────────────────────────────────────────────────
1) ONE STEP AT A TIME: Do not output a full roadmap. Output ONLY the next high-level step and its sub-steps.
2) STATE AWARENESS: You MUST Analyze the deficiencie's before deciding. 
3) You must not output step that has multi domain task meaning if the list of deficiencies has lets say e.g x,y,z and x,y,z are not related you should determine the most important one and output the next step for implementing, lets say if x was the most important you output the next step for x
4) You should output the high level step explain it and decompose it into substeps for implementation
5) CODABLE SUB-STEPS: Sub-steps must be concrete engineering tasks.
6) NO META TASKS: Do not include "reviewing," "testing," or "planning" as sub-steps. Every sub-step must produce code.


────────────────────────────────────────────────────────
WALKTHROUGH
────────────────────────────────────────────────────────

1-You will recieve a request that will trigger you to output the next step
2-You should analyse the project requirement's and the list of deficiencies and decide the most important thing to implement from this list of deficiencie's
3-Then out of those deficiencie's you should output the most critical one that should be implemented next and that's the high-level step then you output the substeps to implement this step and you are done.


────────────────────────────────────────────────────────
OUTPUT FORMAT (MANDATORY)
────────────────────────────────────────────────────────
You must output exactly this structure:

Number of Substeps: N
NEXT HIGH-LEVEL STEP: <Description of Step N>

Substeps:
N.1 <Description>
N.2 <Description>
...

(Note: No conversational filler, no prefaces, no conclusions. Just the header and the list.)

Number of Substeps should be at the very top of the response
"""


critciser_sys_prompt =  """
You are a criticiser agent in a software developing multi agent pipeline
Here is the Walkthrough:
you receive a final_requirements document which has detail's for a specifc request that has been asked by a user
also you have two tool's one is files_list which return the list of current existing file name's
and read_file which let's you read content's of the file's.
upon receiving the final requirement's document you should call the files list so you can see what file's exist currently
then you should ask for a read for each file to understand the current status of the project then and only then:
you should output the list of deficiencies that is holding the project from becoming what the user expect's meaning thing's that is not yet implemented
or maybe implemented but not in a perfect way, your output will help the pipeline to implement these deficiencies sequentially therefore also you must output the list
of deficiencies in a sorted way starting from the most important to least and a rule of thumb that we want is that you review the project thoroughly and output deficiencies
we dont want general view for just a few file's and just output anything, because you the critiser agent will be used along the implementation so different version's of you will run whenever
we finish some implementation so we dont want just general view we want thorough view and speicfic deficiencies based on the current state.






"""
def remove_header_line(text):
   lines = text.splitlines()
   for i,line in enumerate(lines):
      if line.strip():
         return "\n".join(lines[i+1:])
   return ""
          
tools = [
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
        "name": "read_file",
        "description": "Read the content of an existing file from the disk.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]



def run_agent2(file_state):
    files_list = file_state.files_list

    def read_file(path):
        path1 = os.path.join("Pipeline",path)
        print(f"THE Criticiser REQUESTED A READ FOR:{path}")
        p = Path(path1)
        if not p.exists():
            print(f"The requested file does not exist")
            return f"[READ_FILE_ERROR] FILE NOT FOUND {path}"
        with open(path1,'r',encoding='utf-8') as f:
            return f.read()

    tool_map = {
        'read_file': read_file,
        'files_list': files_list,
    }



    with open("final_requirements.txt",'r',encoding='utf-8') as f:
       agent1_output = f.read()
   

   

    print(f"{Back.CYAN}{Fore.LIGHTBLACK_EX} Starting Agent 2")
    print("INPUT FROM AGENT 1:")
    print(f"{Fore.GREEN} {agent1_output}")
   
   

    model = "gpt-4.1-mini"
    criticiser = BaseAgent(client=client,model=model,system_prompt=critciser_sys_prompt,tools=tools,tool_map=tool_map,temperature=0)
   
    deficiency_list = criticiser.chat(f"PROJECT REQUIREMENTS: {agent1_output}\n This is the project requirement's by the user now go and analyse the current state of the project and output a list of the current deficiencie's or maybe problem's")
    print(f"{Back.BLUE}Deficiency list:{deficiency_list}")
    Agent2 =  BaseAgent(client=client,model=model,system_prompt=Decomposer_sys_prompt,temperature=0)
    response = Agent2.chat(f"{deficiency_list} This is the current deficiencie's and problem's in the project identify the most important thing to go for next and output it")
   
    match  = re.search(r"Number\s+Of\s+Substeps\s*:\s*(\d+)",response,re.IGNORECASE)
    if not match:
        raise ValueError("Number of steps header not found or malformed")
    match = match.group(1)

    print(f"{Style.RESET_ALL}{Fore.LIGHTBLACK_EX}OUTPUT FROM AGENT 2:")
    print(f"{Fore.YELLOW} {response}{Style.RESET_ALL}")

    cleaned_response = remove_header_line(response)

    with open("Step.txt",'w') as f:
        f.write(cleaned_response)
    
    with open("Substeps_counts.txt",'w') as f:
        f.write(match)
   

    
    return response
