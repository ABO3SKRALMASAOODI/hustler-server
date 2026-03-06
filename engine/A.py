from Agent4 import create_reviewer

from colorama import Style,Fore,Back
from Agent1 import run_agent1
from Agent2 import run_agent2

from Agent3 import run_agent3

from Agent5 import create_generator
from File_state import FileState
from dotenv import load_dotenv

load_dotenv()

case1 = input(f"{Fore.YELLOW}Do you want to start from scratch? ")
if case1=='yes':
    files_list = FileState()
    #run_agent1()
    run_agent2(files_list)
    
    
    substep = 0
    
else:
    files_list = FileState(False)
    
    with open("current_substep.txt",'r',encoding='utf-8') as f:
        substep = int(f.read())
with open("Substeps_counts.txt",'r') as f:
    threshold = int(f.read())

i=0
while True:
    while True:
        substep+=1
        reviewer_input = run_agent3(substep)
        reviewer4 = create_reviewer(files_list)
        generator5 = create_generator(reviewer4,files_list=files_list)
        while True:
            rev_output = reviewer4.chat(reviewer_input)
            print(f"{Fore.YELLOW}Reviewer Output:\n {rev_output}")
            reviewer_input = generator5.chat(rev_output)
            if "ACCOMPLISHED" in rev_output:
                break
            print(f"{Fore.RED}Generator Output:\n {reviewer_input}")
            #state = input("continue?").strip().lower()
            #if state =="yes":
            #    continue
            #elif state=="chat":
            #    reviewer_input = input("what do you want to tell the reviewer:")
            #elif state=="break":
            #    break
            
        if threshold <= substep:
            
            substep=0
            run_agent2(file_state=files_list)
            with open("Substeps_counts.txt",'r') as f:
                threshold = int(f.read())
            print("completed a complete step exiting to the outer loop")
            break    

        #status = input("continue the second loop?")
        #if status=='no':
        #    break
    i+=1
    if i%3==0:    
        status = input("continue the first loop?")
        if status=='no':
    
            with open("current_substep.txt", 'w') as f:
                f.write(str(substep))
        
            break
