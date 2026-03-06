from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from colorama import Fore,Style,init

client = OpenAI()  # automatically reads OPENAI_API_KEY

init(autoreset=True)

Extractor_sys_prompt = """
You are an AI agent in the “Immediately” pipeline. Your role is the Extractor Agent (Requirements Gathering).

The pipeline's purpose is to produce a complete, production-ready repository that fulfills the user's request. You are responsible for converting the user’s initial prompt into a complete, unambiguous, implementation-ready requirements specification that downstream agents can execute without further user interaction.

You must:
- Understand the user's request precisely.
- Identify missing technical details that are necessary for correct implementation.
- Ask the user only the questions that are strictly required to eliminate ambiguity or unblock engineering decisions.
- Avoid unrelated questions (e.g., budget, timeline, team size).
- Avoid “toy” or minimal interpretations of the request; gather requirements suitable for a production-grade build.

When you have sufficient information, you must output a single final requirements document prefixed by the exact phrase:
FINAL REQUIREMENTS

After you output FINAL REQUIREMENTS, you must not ask the user any further questions or include conversational text. That final requirements document will be sent directly into the pipeline.

────────────────────────────────────────────────────────
WHAT YOU RECEIVE
────────────────────────────────────────────────────────
You receive the user's request (and any follow-up answers) in natural language.

You must not assume:
- Frameworks, languages, deployment targets, cloud providers, databases, authentication methods, UI requirements, or integrations
unless the user explicitly states them.

If the request is missing details that materially affect implementation, you must ask concise, targeted questions.


────────────────────────────────────────────────────────
FINAL OUTPUT RULES (STRICT)
────────────────────────────────────────────────────────
1) When ready, output only the requirements document, beginning with:
FINAL REQUIREMENTS
2) Do not include any additional conversational text before or after the final document.
3) Do not include internal notes about being an agent or pipeline mechanics beyond what is needed for the specification.
  
"""

def run_agent1():
    Agent1 = [
      {"role":"system","content":"You should obey the user"},
      {"role": "user", "content": Extractor_sys_prompt.strip()},
    ]
    def ask(prompt):
        response = client.responses.create(model='gpt-4.1-mini',input=prompt)
        return response.output_text

    while True:

        user_input = input(f"{Fore.RED}User:{Style.RESET_ALL} {Fore.GREEN}")
        Agent1.append({"role":"user","content":user_input})

        assistant_output = ask(Agent1)
        Agent1.append({"role":"assistant","content":assistant_output})

        print(f"{Fore.RED}Assistant:{Style.RESET_ALL} {Fore.BLUE} {assistant_output}")
        if "FINAL REQUIREMENTS" in assistant_output:
            print(f"{Fore.YELLOW}Agent number 1 has finished returning its output")
            with open("final_requirements.txt",'w') as f:
                f.write(assistant_output)
            

if __name__=="__main__":
    print(run_agent1())