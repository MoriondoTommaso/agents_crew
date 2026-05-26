import os
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from langchain_openai import ChatOpenAI

@CrewBase
class CodingAgencyCrew():
    """Hybrid Coding Agency Crew - Orchestrating Local & Cloud LLMs"""

    # Model Configuration
    def __init__(self) -> None:
        # Frontier Model (via FreeLLMAPI) for logic and QA
        self.frontier_llm = ChatOpenAI(
            openai_api_base="http://localhost:3001/v1",
            openai_api_key=os.getenv("FREELLMAPI_KEY"),
            model_name="auto"
        )
        # Local Model (via Ollama) for heavy-lifting code generation
        self.local_llm = ChatOpenAI(
            openai_api_base="http://localhost:11434/v1",
            openai_api_key="ollama",
            model_name="qwen2.5-coder:14b",
            temperature=0.1
        )

    # Agents defined in config/agents.yaml
    @agent
    def senior_architect(self) -> Agent:
        return Agent(
            config=self.agents_config['senior_architect'],
            llm=self.frontier_llm,
            verbose=True
        )

    @agent
    def senior_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['senior_developer'],
            llm=self.local_llm,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            llm=self.frontier_llm,
            verbose=True
        )

    # Tasks defined in config/tasks.yaml
    @task
    def planning_task(self) -> Task:
        return Task(
            config=self.tasks_config['planning_task'],
        )

    @task
    def coding_task(self) -> Task:
        return Task(
            config=self.tasks_config['coding_task'],
        )

    @task
    def review_task(self) -> Task:
        return Task(
            config=self.tasks_config['review_task'],
            output_file='report.md' # Saves the final output to a file
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Coding Agency crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )