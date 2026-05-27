from dotenv import load_dotenv
from crew import CodingAgencyCrew

load_dotenv()

def run():
    inputs = {
        'topic':           'Graph Theory Spectral Indexing',
        'language':        'Python',
        'user_request':    'Create a high-performance script to calculate the Normalized Laplacian Matrix and the Fiedler Vector of a sparse graph.',
        'review_feedback': '',
    }

    print("\ud83d\ude80 Initializing the Hybrid Autonomous Agency...")
    result = CodingAgencyCrew().run_with_healing(inputs=inputs)

    print("\n######################")
    print("## FINAL OUTPUT")
    print("######################\n")
    print(result)

if __name__ == "__main__":
    run()
