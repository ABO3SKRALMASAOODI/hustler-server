from openai import OpenAI
from dotenv import load_dotenv
from colorama import Fore,Style,Back
load_dotenv()

client = OpenAI()

combiner_sys_prompt = """
You are Agent 3 in the AI pipeline system called "Immediately." Your role is the Combiner Agent.

Your mission is to prepare a clear, self-contained execution brief for the Reviewer Agent (Agent 4).  
The Reviewer has no prior context and will see only your output before delegating the task to the code generator.

Inputs you are given:
- The project idea.
- The one high-level step.
- The full list of sub-steps for the one high-level step e.g (n.k).
- A substep timestep indicating which sub-step is currently being executed.

Your output must enable execution of **one sub-step only** n.k.

You must:
1. Briefly summarize what we are building, limited strictly to context required for the current step.
2. Present the single high-level step.
3. Extract and clearly explain the single sub-step that matches the current substep index.


Formatting requirements:
- Use clear section headers.
- Be explicit and concrete.
- Write as if we are actively building this together.
- Do not describe the pipeline, agent roles, or future steps.
- Do not include instructions about reviewing, critiquing, or generating code.
- Do not add commentary before or after the structured content.

Your output must give the Reviewer everything needed to confidently implement this sub-step at the highest possible quality that is realistically achievable.

End immediately after the structured brief.

the steps are not zero based so e.g step one means step one 
"""



def run_agent3(substep):

    
    with open("final_requirements.txt",'r',encoding='utf-8') as f:
        agent1_output = f.read()
   

    with open("Step.txt",'r',encoding="utf-8") as f:
        agent2_output = f.read()


    




    print(f"{Back.CYAN}{Fore.LIGHTBLACK_EX} Starting Agent 3")
    print(f"Substep:{substep}")
    print("INPUT FROM AGENT 1:")
    print(f"{Fore.GREEN} {agent1_output}")
    print("INPUT FROM AGENT 2:")
    print(f"{Fore.RED} {agent2_output}")
    
    messages = [
        {"role":"system","content":"You should obey the user"},
        {"role":"user","content":combiner_sys_prompt},
        {"role":"user","content": f"Substep Timestep:{substep}\nInput from Agent 1: {agent1_output} \n Input from Agent 2: {agent2_output}"}
    ]


    response = client.responses.create(model="gpt-4.1-mini",input=messages)
    response = response.output_text
    
   
    print(f"{Style.RESET_ALL}{Fore.LIGHTBLACK_EX}OUTPUT FROM AGENT 3:")
    print(f"{Fore.LIGHTCYAN_EX} {response}{Style.RESET_ALL}")

    return response

    
    

if __name__ =="__main__":
    run_agent3(2)