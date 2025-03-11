import os
import subprocess
import tempfile

def create_cdk_app(issue_number, reproduction_code):
    """Creates a CDK app with the provided reproduction code."""
    with tempfile.TemporaryDirectory() as temp_dir:
        app_dir = os.path.join(temp_dir, f"issue-{issue_number}")
        os.makedirs(app_dir, exist_ok=True)

        # Initialize a new CDK app
        subprocess.run(["cdk", "init", "app", "--language=typescript"], cwd=app_dir, check=True)
        
        lib_dir = os.path.join(app_dir, "lib")
        stack_file = os.path.join(lib_dir, "app-stack.ts")
        
        # Inject the reproduction code
        with open(stack_file, "w") as f:
            f.write(reproduction_code)
        
        # Run CDK synth to generate the CloudFormation template
        subprocess.run(["cdk", "synth"], cwd=app_dir, check=True)
        
        return app_dir