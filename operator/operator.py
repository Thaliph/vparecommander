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
from typing import Dict, Any

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
    
    # Use token for authentication
    auth_url = f"https://{auth_token}@github.com/{repo_owner}/{repo_name}.git"
    
    try:
        subprocess.run(
            ["git", "clone", "--branch", branch, auth_url, repo_dir],
            check=True, 
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info(f"Successfully cloned repository {repo_url}")
        return repo_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repository: {e}")
        raise kopf.PermanentError(f"Failed to clone git repository: {e}")

def create_branch(repo_dir: str, branch_name: str) -> None:
    """Create a new branch in the repository."""
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "checkout", "-b", branch_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info(f"Created branch {branch_name}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create branch: {e}")
        raise kopf.PermanentError(f"Failed to create git branch: {e}")

def create_patch_file(repo_dir: str, git_path: str, target_resource: Dict[str, Any], resources: Dict[str, str]) -> str:
    """Create the patch file in the repository."""
    # Ensure the patches directory exists
    patch_dir = os.path.join(repo_dir, git_path, "patches")
    os.makedirs(patch_dir, exist_ok=True)
    
    # Generate a unique filename based on the target resource and timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    kind = target_resource.get('kind', '').lower()
    name = target_resource.get('name', '').lower()
    namespace = target_resource.get('namespace', 'default').lower()
    patch_file = os.path.join(patch_dir, f"{timestamp}-{namespace}-{kind}-{name}.yaml")
    
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
        
        # CPU limit patch (typically 2x request)
        cpu_limit = resources.get('cpuLimit', resources['cpu'])
        patches.append({
            'op': 'add',
            'path': f"/spec/template/spec/containers/{container_index}/resources/limits/cpu",
            'value': cpu_limit
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
    
    logger.info(f"Created patch file at {patch_file}")
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
        
        # Commit changes
        subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m", commit_message],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Push changes to create the PR branch
        subprocess.run(
            ["git", "-C", repo_dir, "push", "origin", branch_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        logger.info(f"Successfully committed and pushed changes on branch {branch_name}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e}")
        raise kopf.PermanentError(f"Git operation failed: {e}")

def create_pull_request(repo_dir: str, repo_url: str, branch_name: str, auth_token: str, 
                      title: str, body: str) -> str:
    """Create a pull request using the GitHub CLI or API."""
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
        "base": "main"  # Target the main branch
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
        logger.info(f"Created pull request: {pr_url}")
        return pr_url
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to create pull request: {e}")
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
        logger.error(f"Failed to get VPA: {e}")
        raise kopf.TemporaryError(f"Failed to get VPA: {e}", delay=300)

@kopf.on.create('recommander.k8s.io', 'v1', 'vparecommenders')
@kopf.on.update('recommander.k8s.io', 'v1', 'vparecommenders')
@kopf.on.timer('recommander.k8s.io', 'v1', 'vparecommenders', interval=3600)  # Run every hour
async def recommend_resources(spec, meta, status, namespace, logger, **kwargs):
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
    
    # Get the git authentication token from the secret
    core_v1_api = kubernetes.client.CoreV1Api()
    try:
        secret = core_v1_api.read_namespaced_secret(name=secret_name, namespace=namespace)
        git_token = base64.b64decode(secret.data.get('token')).decode('utf-8')
    except kubernetes.client.rest.ApiException as e:
        logger.error(f"Failed to get secret {secret_name}: {e}")
        raise kopf.PermanentError(f"Failed to get git secret: {e}")
    
    # Get VPA recommendations
    resources = get_vpa_recommendation(vpa_name, vpa_namespace)
    if not resources:
        return {'lastRecommendation': {}, 'status': 'NoRecommendation'}
    
    # Generate unique branch name
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    branch_name = f"vpa-update-{name}-{timestamp}"
    
    # Clone repository and create patch
    try:
        repo_dir = git_clone(git_repo, git_token)
        create_branch(repo_dir, branch_name)
        
        patch_file = create_patch_file(repo_dir, git_path, target_resource, resources)
        
        # Commit message details
        target_kind = target_resource.get('kind', '')
        target_name = target_resource.get('name', '')
        target_ns = target_resource.get('namespace', 'default')
        
        commit_message = f"Update {target_kind}/{target_name} resource limits based on VPA recommendation"
        
        git_commit_and_push(repo_dir, branch_name, commit_message)
        
        # Create pull request
        pr_title = f"Resource update for {target_ns}/{target_kind}/{target_name}"
        pr_body = f"""
This PR was automatically generated by the VPA Recommender operator.

It updates the resource requests and limits for {target_kind} `{target_name}` in namespace `{target_ns}` 
based on the recommendations from VPA `{vpa_name}` in namespace `{vpa_namespace}`.

New recommended values:
- CPU request: {resources.get('cpu', 'not updated')}
- Memory request: {resources.get('memory', 'not updated')}
- CPU limit: {resources.get('cpuLimit', resources.get('cpu', 'not updated'))}
- Memory limit: {resources.get('memoryLimit', resources.get('memory', 'not updated'))}
"""
        
        pr_url = create_pull_request(
            repo_dir, git_repo, branch_name, git_token, pr_title, pr_body
        )
        
        # Update status
        return {
            'lastRecommendation': resources,
            'lastPRUrl': pr_url,
            'lastSuccessfulRunTime': datetime.now().isoformat(),
            'conditions': [{
                'type': 'Recommended',
                'status': 'True',
                'reason': 'PRCreated',
                'message': f'Successfully created PR: {pr_url}',
                'lastTransitionTime': datetime.now().isoformat()
            }]
        }
    except Exception as e:
        logger.exception(f"Failed to process VPA recommendation: {str(e)}")
        return {
            'lastRecommendation': resources,
            'conditions': [{
                'type': 'Recommended',
                'status': 'False', 
                'reason': 'ProcessingError',
                'message': str(e),
                'lastTransitionTime': datetime.now().isoformat()
            }]
        }
