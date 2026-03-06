from openai import OpenAI
from dotenv import load_dotenv
from colorama import Fore,Style,Back
import re
from Agent2 import remove_header_line
load_dotenv()

client = OpenAI()

Decomposer_sys_prompt = """
You are an AI agent in the “Immediately” pipeline. Your role is the Decomposer Agent.

The pipeline's purpose is to generate complete, production-ready repositories for large coding requests through a multi-agent, sequential build process. You receive a detailed, clarified request from the preceding Idea-Extractor agent. Your responsibility is to convert that request into an ordered, dependency-correct build plan that downstream agents can implement step-by-step.

Your output must be a sequence of implementation-oriented steps that can be executed strictly in order. Each step must be “codeable”: it must correspond to concrete engineering work that results in specific, verifiable repository changes. Do not produce generic, non-actionable planning (“initialize repo”, “do setup”, “think about architecture”), and do not produce steps that require artifacts that only appear in later steps.

────────────────────────────────────────────────────────
OBJECTIVE
────────────────────────────────────────────────────────
Transform the incoming detailed request into a set of sequential steps such that:
1) Each step can be implemented independently once prior steps are done.
2) Each step produces tangible code changes and/or tests.
3) Dependencies are respected (no forward references).
4) The plan naturally builds infrastructure before features that depend on it.
5) The steps collectively cover the full request with production quality.

────────────────────────────────────────────────────────
STRICT RULES
────────────────────────────────────────────────────────
1) Output steps only. Do not include long explanations, essays, or commentary.
2) Each step must be implementable as code changes, not abstract advice.
3) Every step must have a clear, verifiable outcome (build passes, tests pass, endpoint works, UI renders, etc.).
4) Avoid “meta” steps (e.g., “review”, “analyze”, “decide tech stack”, “setup repository”).
   - If foundational work is required, express it as concrete deliverables (e.g., “Add config X with values Y and ensure command Z runs”).
5) Do not assume frameworks or technologies unless the request explicitly specifies them.
   - If the request is ambiguous, include a step to implement a minimal but production-sound selection mechanism ONLY if the pipeline’s inputs guarantee a chosen stack; otherwise, keep steps stack-agnostic and interface-driven.
6) Keep steps granular but meaningful:
   - Too large: “Build the whole backend” is invalid.
   - Too small: “Create folder structure” is invalid.
7) No step may depend on work that is scheduled for a later step.
8) At the very top of the respond output something like
   Number Of Steps: N


────────────────────────────────────────────────────────
QUALITY BAR
────────────────────────────────────────────────────────
Your plan must be detailed enough that an implementation agent can begin coding immediately with no guesswork about what “done” means for a step.

Begin when you receive the detailed request from the Idea-Extractor agent.


"""

def run_agent2():
    with open('final_requirements.txt','r',encoding='utf-8') as f:
        agent1_output = f.read()
    print(f"{Back.CYAN}{Fore.LIGHTBLACK_EX} Starting Agent 2")
    print("INPUT FROM AGENT 1:")
    print(f"{Fore.RED} {agent1_output}")
    messages = [
        {"role":"system","content":"You should obey the user"},
        {"role":"user","content":Decomposer_sys_prompt},
        {"role":"user","content": f"Input from Agent 1 (the idea extractor from the user): {agent1_output}"}
    ]

    response = client.responses.create(model="gpt-4.1-mini",input=messages)
    response = response.output_text
    
    match  = re.search(r"Number\s+Of\s+Steps\s*:\s*(\d+)",response,re.IGNORECASE)
    if not match:
        raise ValueError("Number of steps header not found or malformed")
    match = match.group(1)
    print(f"{Style.RESET_ALL}{Fore.LIGHTBLACK_EX}OUTPUT FROM AGENT 2:")
    print(f"{Fore.YELLOW} {response}{Style.RESET_ALL}")

    cleaned_response = remove_header_line(response)
    with open("High_level_steps.txt",'w') as f:
        f.write(cleaned_response)
    
    with open("High_level_steps_count.txt",'w') as f:
        f.write(match)
    

    

if __name__ =="__main__":
    print(f"{Fore.YELLOW}",run_agent2("""FINAL REQUIREMENTS DOCUMENT

Project Title:
Chatbot Website Similar to ChatGPT

Purpose and Goals:
- Build a chatbot website that allows users to interact with an AI-powered chatbot interface similar in functionality to ChatGPT.
- Provide an intuitive, modern platform accessible to everyone where users can chat with the AI and get intelligent, conversational responses.
- Support a wide range of chatbot features and capabilities to maximize user engagement and practical utility.

Target Audience:
- General public / everyone.
- Users interested in AI chatbots for queries, conversation, assistance, and more.

Content and Features:
- Core chatbot interface for live interactions with the AI model.
- User can input any text prompt and receive AI-generated responses in real-time.
- Support typical chatbot functionalities:
  • Multi-turn conversations with context awareness.
  • Display conversation history in a clean, readable format.
  • Input text box with submission via button or “Enter” key.
  • Loading and error states handling.
  • Clear/reset chat option.
  • Optionally, ability to adjust chatbot settings such as response length, creativity (temperature).
- No user authentication or account system required (unless implicitly useful).
- The backend should connect to a proper AI language model API (e.g., OpenAI's GPT API) or a local AI inference service.
- Analytics or usage tracking is optional but can be considered.
- Ensure the chatbot supports general conversation, question answering, creative writing, and other typical GPT use cases.

Design and Style:
- Modern, minimalist, and user-friendly design.
- Responsive UI to support desktop and mobile devices.
- Clean typography, intuitive layout, pleasant color scheme (dark mode optional).
- Smooth user experience with animations for loading states or message arrival.
- Accessible design considerations (contrast, font size).

Technical Requirements:
- Frontend: Use a modern JavaScript framework (React preferred for modularity and community support).
- Backend: Node.js/Express or similar to serve frontend and handle chatbot API requests.
- Integration with an AI chatbot API like OpenAI GPT-4 API or equivalent.
- No strict preference on exact tech stack; however, solution should be scalable and maintainable.
- Responsive design for mobile and desktop.
- Hosting and deployment setup instructions included but no hosting/domain provided.

Domain and Hosting:
- No domain or hosting currently available.
- Deliverables will include instructions/recommendations for deploying on platforms such as Vercel, Netlify (front end), and Heroku, AWS, or similar (backend).

Budget and Timeline:
- No budget or timeline constraints provided.
- Design and scope optimized for a minimal viable product with core chatbot functionality and clean modern UI.

Additional Notes:
- No additional special requirements noted.
- Prioritize ease of use and smooth interaction.
- Ensure code quality and documentation for ease of future enhancements.

Summary:
Develop a scalable, modern chatbot website similar to ChatGPT that supports conversational AI interactions accessible to all users. The website should feature a clean, responsive design and integrate an AI model backend capable of handling diverse chatbot queries and conversations. Delivery includes full source code, documentation, and deployment guidance."""))


    



