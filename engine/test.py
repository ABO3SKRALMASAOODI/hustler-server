from openai import OpenAI
from colorama import Fore,Style,Back
from dotenv import load_dotenv
load_dotenv()
client = OpenAI()

Extractor_sys_prompt = """
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


────────────────────────────────────────────────────────
QUALITY BAR
────────────────────────────────────────────────────────
Your plan must be detailed enough that an implementation agent can begin coding immediately with no guesswork about what “done” means for a step.

Begin when you receive the detailed request from the Idea-Extractor agent.


"""

Agent1 = [
    {"role": "system", "content": "you should obey user requests"},
    {"role":"user","content":Extractor_sys_prompt}
    ]
def ask(prompt):
    response = client.responses.create(model='gpt-4.1-mini',input=prompt)
    return response.output_text

ans = ask(Agent1)
print(ans)
Agent1.append({"role":"assistant","content":ans})
while True:

    user_input =  """FINAL REQUIREMENTS

Project Overview:
Develop a fully responsive e-commerce website for a women’s clothing merchant, designed and built to match the quality, aesthetics, and functionality of high-end fashion websites such as Prada or Louis Vuitton.

Key Requirements:

1. Platform & Design:
   - Fully responsive website optimized for desktop, tablet, and mobile devices.
   - High-end luxury fashion aesthetic with a modern, clean, and elegant design.
   - Support for high-resolution product images and videos with zoom-in functionality.
   - Admin interface to upload and manage product images and videos.

2. Language & Currency:
   - English language only.
   - Pricing and transactions exclusively in USD.

3. Product Catalog & Shopping:
   - Detailed product catalog with categories relevant to women’s clothing.
   - Advanced product filtering and sorting options by parameters such as size, color, price, and collection.
   - Each product page to include high-quality images/videos, detailed descriptions, available sizes/colors, price, and any relevant details.
   
4. User Accounts:
   - User registration and login functionality, with secure authentication.
   - User profile management.
   - Ability for users to view their order history.
   - Wishlist/favorites functionality.

5. Shopping Cart & Checkout:
   - Fully functional shopping cart allowing users to add, remove, and modify items.
   - Checkout process integrating Apple Pay as the payment gateway.
   - Secure handling of payment data in compliance with best practices.

6. Order Management:
   - Users can track their orders via their account.
   - Admin dashboard for managing orders, products, and user data.

7. Hosting & Deployment:
   - No specific hosting or deployment preferences specified; solution to be designed to support typical modern web hosting environments.

8. Legal & Compliance:
   - No specific legal or compliance requirements specified (e.g., no GDPR or accessibility mandates noted).

9. Miscellaneous:
   - No multi-language support or regional settings other than English/USD.
   - No integration with third-party logistics, CRM, or editorial content such as brand stories or news.

Deliverables:
- Complete source code with clear documentation.
- Admin panel to manage products, images, videos, orders, and users.
- Instructions for deployment and hosting environment setup.
- Responsive design verified across major browsers and devices. 

Testing:
- Thorough functional testing of shopping cart, checkout, user accounts, filtering, and media upload.
- Cross-device and cross-browser compatibility testing.
- Security testing focused on user data and payment processes. """
    Agent1.append({"role":"user","content":user_input})

    assistant_output = ask(Agent1)
    Agent1.append({"role":"assistant","content":assistant_output})

    print(f"{Fore.RED}Assistant:{Style.RESET_ALL} {Fore.BLUE} {assistant_output}")
   