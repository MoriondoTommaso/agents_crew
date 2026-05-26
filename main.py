from dotenv import load_dotenv
from crew import CodingAgencyCrew

load_dotenv()

def run():
    # Define the specific inputs for our cutting-edge agents
    inputs = {
        'topic': 'Graph Theory Spectral Indexing',
        'language': 'Python',
        'user_request': 'Create a high-performance script to calculate the Normalized Laplacian Matrix and the Fiedler Vector of a sparse graph.'
    }
    
    print("🚀 Initializing the Hybrid Autonomous Agency...")
    result = CodingAgencyCrew().crew().kickoff(inputs=inputs)
    
    print("\n######################")
    print("## FINAL OUTPUT")
    print("######################\n")
    print(result)

if __name__ == "__main__":
    run()