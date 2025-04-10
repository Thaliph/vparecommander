#!/usr/bin/env python3

import kopf
import kubernetes
import os
import tempfile
import base64
import yaml
import json
from datetime import datetime
import subprocess
import logging
import re
from typing import Dict, Any, Tuple, List, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vpa-recommender")

def git_clone(repo_url: str, auth_token: str, branch: str = "main") -> str:
    """Clone the git repository and return the temporary directory path."""
    repo_dir = tempfile.mkdtemp()
    
    # Extract the repo owner and name from the URL
    # Assuming format like: https://github.com/owner/repo.git
    parts = repo_url.rstrip('.git').split('/')
    repo_owner = parts[-2]
    repo_name = parts[-1]
    
    # Strip any whitespace or newline characters from token
    auth_token = auth_token.strip()
    
    # Use token for authentication
    auth_url = f"https://{auth_token}@github.com/{repo_owner}/{repo_name}.git"
    
    try:
        logger.info(f"Cloning repository {repo_url}...")
        # Clone the default branch first
        subprocess.run(
            ["git", "clone", auth_url, repo_dir],
            check=True, 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        logger.info(f"Successfully cloned repository {repo_url}")
        return repo_dir
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode('utf-8') if e.stderr else "No error output"
        logger.error(f"Failed to clone repository: {e}, Error output: {error_output}")
        raise kopf.PermanentError(f"Failed to clone git repository: {e}")

def check_branch_exists(repo_dir: str, branch_name: str) -> bool:
    """Check if a branch exists locally or remotely."""
    try:
        # Fetch all branches including remote ones
        subprocess.run(
            ["git", "-C", repo_dir, "fetch", "--all"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Check if branch exists
        result = subprocess.run(
            ["git", "-C", repo_dir, "ls-remote", "--heads", "origin", branch_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # If output contains the branch name, it exists
        return len(result.stdout) > 0
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error checking branch existence: {e}")
        return False

def create_or_checkout_branch(repo_dir: str, branch_name: str) -> None:
    """Create a new branch or checkout existing one in the repository."""
    try:
        if check_branch_exists(repo_dir, branch_name):
            # Checkout existing branch
            subprocess.run(
                ["git", "-C", repo_dir, "checkout", "-B", branch_name, f"origin/{branch_name}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Checked out existing branch {branch_name}")
        else:
            # Create new branch
            subprocess.run(
                ["git", "-C", repo_dir, "checkout", "-b", branch_name],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Created new branch {branch_name}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create/checkout branch: {e}")
        raise kopf.PermanentError(f"Failed to create/checkout git branch: {e}")

def create_patch_file(repo_dir: str, git_path: str, target_resource: Dict[str, Any], 
                    resources: Dict[str, str]) -> str:
    """Create the patch file in the repository with specific naming format."""
    # Ensure the patches directory exists
    patch_dir = os.path.join(repo_dir, git_path, "patches")
    os.makedirs(patch_dir, exist_ok=True)
    
    # Use the target resource name and kind for the filename
    kind = target_resource.get('kind', '').lower()
    name = target_resource.get('name', '').lower()
    
    # Create the filename: <name-of-deployment>.<kind-of-resource>.yaml
    patch_file = os.path.join(patch_dir, f"{name}.{kind}.yaml")
    
    container_index = target_resource.get('containerIndex', 0)
    
    # Create the patch content
    patches = []
    
    # CPU request patch
    if 'cpu' in resources:
        patches.append({
            'op': 'add',
            'path': f"/spec/template/spec/containers/{container_index}/resources/requests/cpu",
            'value': resources['cpu']
        })
    
    # Memory request patch
    if 'memory' in resources:
        patches.append({
            'op': 'add',
            'path': f"/spec/template/spec/containers/{container_index}/resources/requests/memory",
            'value': resources['memory']
        })
        
        # Memory limit patch
        memory_limit = resources.get('memoryLimit', resources['memory'])
        patches.append({
            'op': 'add',
            'path': f"/spec/template/spec/containers/{container_index}/resources/limits/memory",
            'value': memory_limit
        })
    
    # Write the patches to the file
    with open(patch_file, 'w') as f:
        yaml.dump(patches, f)
    
    logger.info(f"Created/Updated patch file at {patch_file}")
    return patch_file

def git_commit_and_push(repo_dir: str, branch_name: str, commit_message: str) -> None:
    """Commit the changes and push to the repository."""
    try:
        # Add all changes
        subprocess.run(
            ["git", "-C", repo_dir, "add", "."],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Set git identity for the commit
        subprocess.run(
            ["git", "-C", repo_dir, "config", "user.email", "vpa-recommender@k8s.io"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        subprocess.run(
            ["git", "-C", repo_dir, "config", "user.name", "VPA Recommender Bot"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Check if there are changes to commit
        status_result = subprocess.run(
            ["git", "-C", repo_dir, "status", "--porcelain"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        if not status_result.stdout:
            logger.info("No changes to commit")
            return
        
        # Commit changes
        subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m", commit_message],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Push changes to create or update the PR branch
        subprocess.run(
            ["git", "-C", repo_dir, "push", "-f", "origin", branch_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        logger.info(f"Successfully committed and pushed changes on branch {branch_name}")
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode('utf-8') if e.stderr else "No error output"
        logger.error(f"Git operation failed: {e}, Error output: {error_output}")
        raise kopf.PermanentError(f"Git operation failed: {e}")

def check_pull_request_exists(repo_url: str, branch_name: str, base_branch: str, auth_token: str) -> Tuple[bool, Optional[dict]]:
    """Check if a pull request exists from the branch to the base branch."""
    # Extract the repo owner and name from the URL
    parts = repo_url.rstrip('.git').split('/')
    repo_owner = parts[-2]
    repo_name = parts[-1]
    
    # Use GitHub API to list pull requests
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls?head={repo_owner}:{branch_name}&base={base_branch}&state=open"
    
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "GET", 
                "-H", f"Authorization: token {auth_token}",
                "-H", "Accept: application/vnd.github.v3+json",
                api_url
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        response = json.loads(result.stdout)
        
        if response and len(response) > 0:  # Fixed: replaced && with 'and'
            pr_data = {
                'number': response[0]['number'],
                'url': response[0]['html_url'],
                'created_at': response[0]['created_at'],
            }
            return True, pr_data
        return False, None
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        logger.error(f"Failed to check for pull requests: {e}")
        return False, None

def get_commit_count(repo_url: str, branch_name: str, auth_token: str) -> int:
    """Get the number of commits on a branch."""
    # Extract the repo owner and name from the URL
    parts = repo_url.rstrip('.git').split('/')
    repo_owner = parts[-2]
    repo_name = parts[-1]
    
    # Use GitHub API to get branch info
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/branches/{branch_name}"
    
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "GET", 
                "-H", f"Authorization: token {auth_token}",
                "-H", "Accept: application/vnd.github.v3+json",
                api_url
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        response = json.loads(result.stdout)
        
        # Get commit SHA
        commit_sha = response.get('commit', {}).get('sha', '')
        if not commit_sha:
            return 0
            
        # Get commit count
        count_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits?sha={commit_sha}"
        count_result = subprocess.run(
            [
                "curl", "-s", "-X", "GET", 
                "-H", f"Authorization: token {auth_token}",
                "-H", "Accept: application/vnd.github.v3+json",
                count_url
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        count_response = json.loads(count_result.stdout)
        return len(count_response)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        logger.error(f"Failed to get commit count: {e}")
        return 0

def create_pull_request(repo_url: str, branch_name: str, base_branch: str, auth_token: str, 
                      title: str, body: str) -> Dict[str, Any]:
    """Create a pull request using the GitHub API."""
    # Extract the repo owner and name from the URL
    parts = repo_url.rstrip('.git').split('/')
    repo_owner = parts[-2]
    repo_name = parts[-1]
    
    # Use GitHub API through curl to create a PR
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"
    data = {
        "title": title,
        "body": body,
        "head": branch_name,
        "base": base_branch
    }
    
    try:
        result = subprocess.run(
            [
                "curl", "-X", "POST", 
                "-H", f"Authorization: token {auth_token}",
                "-H", "Accept: application/vnd.github.v3+json",
                "-d", json.dumps(data),
                api_url
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        response = json.loads(result.stdout)
        pr_url = response.get("html_url")
        pr_number = response.get("number")
        pr_created_at = response.get("created_at")
        
        logger.info(f"Created pull request: {pr_url}")
        
        return {
            "url": pr_url,
            "number": pr_number,
            "created_at": pr_created_at
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        error_output = e.stderr.decode('utf-8') if hasattr(e, 'stderr') and e.stderr else "No error output"
        logger.error(f"Failed to create pull request: {e}, Error: {error_output}")
        raise kopf.PermanentError(f"Failed to create pull request: {e}")

def get_vpa_recommendation(name: str, namespace: str) -> Dict[str, str]:
    """Get recommendations from a VPA resource."""
    api = kubernetes.client.CustomObjectsApi()
    
    try:
        vpa = api.get_namespaced_custom_object(
            group="autoscaling.k8s.io",
            version="v1",
            namespace=namespace,
            plural="verticalpodautoscalers",
            name=name
        )
        
        # Extract recommendations
        recommendation = vpa.get('status', {}).get('recommendation', {}).get('containerRecommendations', [])
        if not recommendation:
            logger.warning(f"No recommendations found for VPA {name} in namespace {namespace}")
            return {}
            
        # Typically we'd take the first container's recommendation
        # but we might need to match the container name in more complex cases
        container_rec = recommendation[0]
        
        resources = {}
        if 'target' in container_rec:
            if 'cpu' in container_rec['target']:
                resources['cpu'] = container_rec['target']['cpu']
            if 'memory' in container_rec['target']:
                resources['memory'] = container_rec['target']['memory']
        
        logger.info(f"Retrieved recommendations: {resources}")
        return resources
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.error(f"VPA {name} not found in namespace {namespace}. Make sure the VPA exists and has recommendations.")
            # Return empty dict instead of raising, so we don't trigger retries for a resource that doesn't exist
            return {}
        else:
            logger.error(f"Failed to get VPA: {e}")
            raise kopf.TemporaryError(f"Failed to get VPA: {e}", delay=300)

@kopf.on.create('recommander.k8s.io', 'v1', 'vparecommenders')
@kopf.on.update('recommander.k8s.io', 'v1', 'vparecommenders')
@kopf.on.timer('recommander.k8s.io', 'v1', 'vparecommenders', interval=3600, idle=20)  # Run every hour, specify idle time
async def recommend_resources(spec, meta, namespace, logger, **kwargs):
    """Handler for processing VPA recommendations and creating patches."""
    name = meta.get('name')
    logger.info(f"Processing VPARecommender {name}")

    # Extract spec values
    vpa_name = spec.get('vpaName')
    vpa_namespace = spec.get('vpaNamespace')
    git_repo = spec.get('gitRepo')
    git_path = spec.get('gitPath')
    target_resource = spec.get('targetResource', {})
    secret_name = spec.get('secretRef')
    base_branch = spec.get('baseBranch', 'main')  # Default to 'main' if not specified
    
    # Use a fixed branch name for VPA recommendations
    branch_name = "vpar/proposition"
    
    # Get the git authentication token from the secret
    core_v1_api = kubernetes.client.CoreV1Api()
    try:
        secret = core_v1_api.read_namespaced_secret(name=secret_name, namespace=namespace)
        git_token = base64.b64decode(secret.data.get('token')).decode('utf-8')
        # Strip any whitespace or newlines
        git_token = git_token.strip()
        logger.debug(f"Retrieved token (length: {len(git_token)})")
    except kubernetes.client.rest.ApiException as e:
        logger.error(f"Failed to get secret {secret_name}: {e}")
        raise kopf.PermanentError(f"Failed to get git secret: {e}")
    
    # Get VPA recommendations
    resources = get_vpa_recommendation(vpa_name, vpa_namespace)
    if not resources:
        logger.info(f"No recommendations available for VPA {vpa_name} in namespace {vpa_namespace}")
        status_data = {
            'lastRecommendation': {},
            'status': 'NoRecommendation',
            'conditions': [{
                'type': 'Recommended',
                'status': 'False',
                'reason': 'NoRecommendations',
                'message': f'No recommendations available for VPA {vpa_name}',
                'lastTransitionTime': datetime.now().isoformat()
            }]
        }
        await update_status(name, namespace, status_data)
        return
    
    # Check if a pull request already exists from branch_name to base_branch
    pr_exists, pr_data = check_pull_request_exists(git_repo, branch_name, base_branch, git_token)
    
    # Clone repository and update/create patch file
    try:
        # Clone the repository first
        repo_dir = git_clone(git_repo, git_token)
        
        # Now check if branch exists and get commit count after we have the repo
        commit_count = 0
        has_branch = check_branch_exists(repo_dir, branch_name)
        
        # Get commit count if the branch exists
        if has_branch:
            try:
                commit_count = get_commit_count(git_repo, branch_name, git_token)
            except Exception as e:
                logger.warning(f"Failed to get commit count: {e}")
                # Continue even if we can't get commit count
                commit_count = 0
        
        # Checkout or create the branch
        create_or_checkout_branch(repo_dir, branch_name)
        
        # Create/update the patch file with the naming convention
        patch_file = create_patch_file(repo_dir, git_path, target_resource, resources)
        
        # Commit message details
        target_kind = target_resource.get('kind', '')
        target_name = target_resource.get('name', '')
        target_ns = target_resource.get('namespace', 'default')
        
        commit_message = f"Update {target_kind}/{target_name} resource limits based on VPA recommendation"
        
        # Commit and push changes
        git_commit_and_push(repo_dir, branch_name, commit_message)
        
        # Update commit count after the push
        try:
            commit_count = get_commit_count(git_repo, branch_name, git_token)
        except Exception as e:
            logger.warning(f"Failed to get updated commit count: {e}")
        
        # Create pull request if one doesn't exist
        pr_status = {}
        if not pr_exists:
            pr_title = f"Resource update for {target_ns}/{target_kind}/{target_name} from VPA recommendations"
            pr_body = f"""
This PR was automatically generated by the VPA Recommender operator.

It updates the resource requests and limits for {target_kind} `{target_name}` in namespace `{target_ns}` 
based on the recommendations from VPA `{vpa_name}` in namespace `{vpa_namespace}`.

New recommended values:
- CPU request: {resources.get('cpu', 'not updated')}
- Memory request: {resources.get('memory', 'not updated')}
"""
            
            pr_data = create_pull_request(
                git_repo, branch_name, base_branch, git_token, pr_title, pr_body
            )
            
            pr_status = {
                'url': pr_data.get('url'),
                'number': pr_data.get('number'),
                'created_at': pr_data.get('created_at'),
                'commits': commit_count
            }
        else:
            # PR already exists, just update status
            pr_status = {
                'url': pr_data.get('url'),
                'number': pr_data.get('number'),
                'created_at': pr_data.get('created_at'),
                'commits': commit_count
            }
        
        # Update status
        status_data = {
            'lastRecommendation': resources,
            'lastPatch': {
                'time': datetime.now().isoformat(),
                'path': os.path.basename(patch_file),
                'target': f"{target_kind}/{target_name}"
            },
            'pullRequest': pr_status,
            'conditions': [{
                'type': 'Recommended',
                'status': 'True',
                'reason': 'PatchCreated',
                'message': f'Successfully created/updated patch and {"updated" if pr_exists else "created"} PR',
                'lastTransitionTime': datetime.now().isoformat()
            }]
        }
        
        # Use our separate function to update the status
        await update_status(name, namespace, status_data)
        
    except Exception as e:
        logger.exception(f"Failed to process VPA recommendation: {str(e)}")
        # Still update the status with the error
        status_data = {
            'lastRecommendation': resources,
            'conditions': [{
                'type': 'Recommended',
                'status': 'False', 
                'reason': 'ProcessingError',
                'message': str(e),
                'lastTransitionTime': datetime.now().isoformat()
            }]
        }
        await update_status(name, namespace, status_data)

async def update_status(name: str, namespace: str, status_data: Dict) -> None:
    """Update the status of the VPARecommender CR."""
    api = kubernetes.client.CustomObjectsApi()
    
    try:
        # First check if the resource still exists
        try:
            api.get_namespaced_custom_object(
                group="recommander.k8s.io",
                version="v1",
                namespace=namespace,
                plural="vparecommenders",
                name=name
            )
        except kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                logger.warning(f"Resource {namespace}/{name} not found, skipping status update")
                return
            raise  # Re-raise for other API errors
        
        # If resource exists, update its status
        api.patch_namespaced_custom_object_status(
            group="recommander.k8s.io",
            version="v1",
            namespace=namespace,
            plural="vparecommenders",
            name=name,
            body={"status": status_data}
        )
        logger.info(f"Successfully updated status for {namespace}/{name}")
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            # Resource was deleted between our check and the patch - just log and continue
            logger.warning(f"Resource {namespace}/{name} not found during status update, it may have been deleted")
        else:
            logger.error(f"Failed to update status: {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status: {e}")
