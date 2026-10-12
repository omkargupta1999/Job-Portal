"""Skill taxonomy: canonical names, aliases and implications.

Aliases collapse to one canonical skill so "K8s" and "Kubernetes" in the same
posting count once. ``IMPLIES`` expresses that a candidate skill demonstrates
another (e.g. GitLab CI/CD experience implies GitLab and CI/CD); it is only
applied to the candidate's side when matching, never to the job's
requirements.
"""

from __future__ import annotations

import re
from functools import lru_cache

# canonical -> aliases (case-insensitive, matched on word boundaries)
TAXONOMY: dict[str, list[str]] = {
    # Containers & orchestration
    "Kubernetes": ["kubernetes", "k8s", "kube", "k3s"],
    "Red Hat OpenShift": ["openshift", "red hat openshift", "ocp", "openshift container platform", "okd"],
    "Docker": ["docker", "dockerfile", "docker compose", "docker-compose"],
    "Containers": ["containers", "containerization", "containerisation", "container orchestration", "oci images"],
    "Podman": ["podman", "buildah"],
    "Helm": ["helm", "helm charts"],
    "Kustomize": ["kustomize"],
    "Istio": ["istio", "istio service mesh"],
    "Service Mesh": ["service mesh", "linkerd", "consul connect"],
    "Amazon EKS": ["eks", "amazon eks", "elastic kubernetes service"],
    "Azure AKS": ["aks", "azure kubernetes service"],
    "Google GKE": ["gke", "google kubernetes engine"],
    "Rancher": ["rancher"],
    # CI/CD
    "CI/CD": ["ci/cd", "ci / cd", "cicd", "ci-cd", "continuous integration", "continuous delivery",
              "continuous deployment", "build pipelines", "deployment pipelines", "release pipelines"],
    "GitLab CI/CD": ["gitlab ci", "gitlab-ci", "gitlab ci/cd", "gitlab pipelines", "gitlab pipeline",
                     ".gitlab-ci.yml", "gitlab runners", "gitlab runner"],
    "GitLab": ["gitlab"],
    "Jenkins": ["jenkins", "jenkinsfile", "jenkins pipelines"],
    "GitHub Actions": ["github actions", "gh actions"],
    "Azure DevOps": ["azure devops", "azure pipelines", "vsts"],
    "Argo CD": ["argocd", "argo cd", "argo-cd"],
    "Flux": ["fluxcd", "flux cd"],
    "GitOps": ["gitops"],
    "Tekton": ["tekton", "openshift pipelines"],
    "CircleCI": ["circleci", "circle ci"],
    "TeamCity": ["teamcity"],
    "Bamboo": ["bamboo"],
    "Spinnaker": ["spinnaker"],
    "Git": ["git"],
    "GitHub": ["github"],
    "Bitbucket": ["bitbucket"],
    "Maven": ["maven"],
    "Gradle": ["gradle"],
    "JFrog Artifactory": ["artifactory", "jfrog", "jfrog artifactory"],
    "Nexus": ["nexus repository", "sonatype nexus", "nexus"],
    # IaC & config management
    "Terraform": ["terraform", "hcl", "terragrunt"],
    "OpenTofu": ["opentofu"],
    "Pulumi": ["pulumi"],
    "CloudFormation": ["cloudformation", "aws cdk", "cdk"],
    "Ansible": ["ansible", "ansible tower", "awx"],
    "Puppet": ["puppet"],
    "Chef": ["chef"],
    "Packer": ["packer"],
    "Infrastructure as Code": ["infrastructure as code", "iac", "infrastructure-as-code"],
    # Cloud
    "AWS": ["aws", "amazon web services", "ec2", "s3", "iam", "lambda", "cloudwatch", "vpc"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Azure": ["azure", "microsoft azure"],
    "OpenStack": ["openstack"],
    "VMware": ["vmware", "vsphere", "esxi", "vcenter"],
    # Observability
    "Prometheus": ["prometheus", "promql", "alertmanager"],
    "Grafana": ["grafana"],
    "Loki": ["loki", "grafana loki"],
    "EFK Stack": ["efk", "fluentd", "fluent bit", "fluent-bit"],
    "ELK Stack": ["elk", "elasticsearch", "logstash", "kibana", "opensearch"],
    "Datadog": ["datadog"],
    "New Relic": ["new relic", "newrelic"],
    "Splunk": ["splunk"],
    "OpenTelemetry": ["opentelemetry", "otel"],
    "Jaeger": ["jaeger"],
    "Observability": ["observability", "monitoring and alerting", "monitoring", "telemetry"],
    "Dynatrace": ["dynatrace"],
    "Nagios": ["nagios", "zabbix"],
    # Security
    "DevSecOps": ["devsecops", "secure sdlc", "shift left security", "shift-left"],
    "SAST": ["sast", "static application security testing", "static code analysis"],
    "DAST": ["dast", "dynamic application security testing"],
    "SonarQube": ["sonarqube", "sonar", "sonarcloud"],
    "Container Image Scanning": ["image scanning", "container scanning", "container image scanning",
                                 "trivy", "clair", "anchore", "grype", "aqua", "prisma cloud", "twistlock"],
    "Snyk": ["snyk"],
    "Checkmarx": ["checkmarx"],
    "Fortify": ["fortify"],
    "HashiCorp Vault": ["vault", "hashicorp vault"],
    "Secrets Management": ["secrets management", "secret management", "sealed secrets", "external secrets"],
    "Policy as Code": ["opa", "open policy agent", "gatekeeper", "kyverno", "policy as code"],
    "IAM": ["identity and access management", "rbac", "keycloak", "oauth", "saml", "sso"],
    "Compliance": ["pci dss", "pci-dss", "iso 27001", "soc 2", "soc2", "hipaa", "gdpr", "compliance"],
    # Languages & scripting
    "Python": ["python", "python3"],
    "Bash": ["bash", "shell scripting", "shell script", "shell scripts", "sh scripting"],
    "PowerShell": ["powershell"],
    "Go": ["golang", "go lang", "go (golang)"],
    "Java": ["java"],
    "JavaScript": ["javascript", "node.js", "nodejs", "node"],
    "TypeScript": ["typescript"],
    "Ruby": ["ruby"],
    "Rust": ["rust"],
    "Groovy": ["groovy"],
    "YAML": ["yaml"],
    "C#": ["c#", ".net", "dotnet"],
    # OS & networking
    "Linux": ["linux", "rhel", "red hat enterprise linux", "centos", "ubuntu", "debian", "linux administration",
              "unix"],
    "Networking": ["networking", "tcp/ip", "dns", "load balancing", "load balancers", "firewalls", "vpn",
                   "subnetting"],
    "Nginx": ["nginx"],
    "HAProxy": ["haproxy"],
    "Windows Server": ["windows server", "active directory"],
    # Data
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql", "mariadb"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Kafka": ["kafka", "apache kafka"],
    "RabbitMQ": ["rabbitmq"],
    "Oracle Database": ["oracle db", "oracle database"],
    "SQL": ["sql"],
    # Practices
    "Site Reliability Engineering": ["sre", "site reliability", "site reliability engineering", "slo", "slos",
                                     "sli", "error budgets"],
    "Disaster Recovery": ["disaster recovery", "dr drills", "business continuity", "backup and restore"],
    "High Availability": ["high availability", "ha clusters", "fault tolerance"],
    "Performance Tuning": ["performance tuning", "performance optimization", "performance optimisation",
                           "capacity planning", "load testing"],
    "Incident Management": ["incident management", "incident response", "on-call", "on call", "pagerduty",
                            "opsgenie", "production support", "troubleshooting"],
    "Microservices": ["microservices", "micro-services", "microservice architecture"],
    "Serverless": ["serverless"],
    "Agile": ["agile", "scrum", "kanban"],
    "Jira": ["jira", "confluence"],
    "Release Management": ["release management", "release engineering", "build and release"],
    "Platform Engineering": ["platform engineering", "internal developer platform", "backstage"],
    "Cloud Cost Optimization": ["finops", "cost optimization", "cost optimisation"],
    "MLOps": ["mlops", "kubeflow", "mlflow"],
    # Certifications (matched like skills)
    "CKA": ["cka", "certified kubernetes administrator"],
    "CKAD": ["ckad", "certified kubernetes application developer"],
    "CKS": ["cks", "certified kubernetes security specialist"],
    "Red Hat Certification": ["rhce", "rhcsa", "red hat certified", "ex280", "ex188"],
    "AWS Certification": ["aws certified", "aws solutions architect", "aws devops engineer professional"],
    "Azure Certification": ["az-104", "az-400", "azure administrator", "azure devops engineer expert"],
    "GCP Certification": ["google cloud certified", "professional cloud architect", "professional cloud devops"],
    "Terraform Associate": ["terraform associate", "hashicorp certified"],
}

CERTIFICATIONS = {"CKA", "CKAD", "CKS", "Red Hat Certification", "AWS Certification", "Azure Certification",
                  "GCP Certification", "Terraform Associate"}

# A candidate who has the key skill demonstrably has the implied skills.
IMPLIES: dict[str, list[str]] = {
    "GitLab CI/CD": ["GitLab", "CI/CD", "Git"],
    "Jenkins": ["CI/CD"],
    "GitHub Actions": ["CI/CD", "GitHub", "Git"],
    "Azure DevOps": ["CI/CD"],
    "Tekton": ["CI/CD"],
    "Argo CD": ["GitOps", "CI/CD"],
    "Flux": ["GitOps"],
    "Red Hat OpenShift": ["Kubernetes", "Containers"],
    "Amazon EKS": ["Kubernetes", "AWS"],
    "Azure AKS": ["Kubernetes", "Azure"],
    "Google GKE": ["Kubernetes", "GCP"],
    "Kubernetes": ["Containers"],
    "Docker": ["Containers"],
    "Podman": ["Containers"],
    "Terraform": ["Infrastructure as Code"],
    "OpenTofu": ["Infrastructure as Code"],
    "Pulumi": ["Infrastructure as Code"],
    "CloudFormation": ["Infrastructure as Code"],
    "Istio": ["Service Mesh"],
    "Prometheus": ["Observability"],
    "Grafana": ["Observability"],
    "Loki": ["Observability"],
    "EFK Stack": ["Observability"],
    "ELK Stack": ["Observability"],
    "SonarQube": ["SAST"],
    "Checkmarx": ["SAST"],
    "Fortify": ["SAST"],
    "Snyk": ["Container Image Scanning"],
    "HashiCorp Vault": ["Secrets Management"],
    "CKA": ["Kubernetes"],
    "CKAD": ["Kubernetes"],
    "CKS": ["Kubernetes"],
    "PostgreSQL": ["SQL"],
    "MySQL": ["SQL"],
}

# Aliases that are too generic to be detected inside free job text, but are fine
# when the user types them into their own skill list.
_PROFILE_ONLY_ALIASES = {"node", "go (golang)", "kube", "sonar", "nexus", "vault", "chef", "puppet", "aqua",
                         "rust", "ruby", "monitoring", "telemetry", "sso", "iam", "s3", "lambda", "vpc",
                         "unix", "dns", "vpn", "compliance", "troubleshooting", "on call", "node.js", "cdk",
                         "loki", "jaeger", "flux cd", "elk", "sql", "agile", "kanban", "scrum", "serverless",
                         "gdpr", "bamboo", "packer", "spinnaker", "hcl", "git", "aws", "go", "flux", "okd"}
# Keep these detectable in job text even though they look generic.
_TEXT_OK = {"git", "aws", "sql", "loki", "agile", "packer", "serverless", "ruby", "rust", "monitoring",
            "troubleshooting", "on call", "node.js", "unix", "gdpr", "compliance", "dns", "elk"}


def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    return rf"(?<![A-Za-z0-9+#]){escaped}(?![A-Za-z0-9+#])"


@lru_cache(maxsize=2)
def _matcher(for_text: bool) -> tuple[re.Pattern, dict[str, str]]:
    alias_map: dict[str, str] = {}
    for canonical, aliases in TAXONOMY.items():
        for alias in [canonical.lower(), *aliases]:
            alias = alias.lower()
            if for_text and alias in _PROFILE_ONLY_ALIASES and alias not in _TEXT_OK:
                continue
            alias_map.setdefault(alias, canonical)
    ordered = sorted(alias_map, key=len, reverse=True)
    pattern = re.compile("|".join(_alias_pattern(a) for a in ordered), re.IGNORECASE)
    return pattern, alias_map


def canonicalize(name: str) -> str:
    """Map a user-entered skill to its canonical name (or a tidied original)."""
    clean = re.sub(r"\s+", " ", (name or "").strip())
    if not clean:
        return ""
    _, alias_map = _matcher(False)
    return alias_map.get(clean.lower(), clean)


def canonicalize_list(names: list[str]) -> list[str]:
    seen: list[str] = []
    for n in names:
        c = canonicalize(n)
        if c and c.lower() not in {s.lower() for s in seen}:
            seen.append(c)
    return seen


def extract_skills(text: str) -> list[str]:
    """Canonical skills mentioned in free text, in order of first appearance."""
    if not text:
        return []
    pattern, alias_map = _matcher(True)
    found: list[str] = []
    for m in pattern.finditer(text):
        token = m.group(0).lower()
        canonical = alias_map.get(token)
        if canonical and canonical not in found:
            found.append(canonical)
    return found


def expand_with_implications(skills: list[str]) -> set[str]:
    result = {s for s in skills}
    frontier = list(skills)
    while frontier:
        s = frontier.pop()
        for implied in IMPLIES.get(s, []):
            if implied not in result:
                result.add(implied)
                frontier.append(implied)
    return result


_PREFERRED_HEADING = re.compile(
    r"(nice[- ]to[- ]have|preferred|bonus|good to have|plus|desirable|would be a plus|"
    r"what would make you stand out|optional|advantageous)",
    re.IGNORECASE,
)
_HEADING_BREAK = re.compile(
    r"(requirements|qualifications|what you.ll need|what we.re looking for|must[- ]have|"
    r"responsibilities|what you.ll do|about you|benefits|what we offer|about us|your profile|skills)",
    re.IGNORECASE,
)


def split_required_preferred(text: str) -> tuple[list[str], list[str]]:
    """Classify detected skills as required or preferred using section headings.

    Lines after a "nice to have"/"preferred"/"bonus" heading (until the next
    known heading) are treated as preferred. Everything else counts as
    required. A skill mentioned in both is required.
    """
    if not text:
        return [], []
    required_text: list[str] = []
    preferred_text: list[str] = []
    in_preferred = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = len(stripped) <= 80 and (stripped.endswith(":") or stripped.isupper() or len(stripped.split()) <= 6)
        if is_heading and _PREFERRED_HEADING.search(stripped):
            in_preferred = True
            continue
        if is_heading and _HEADING_BREAK.search(stripped):
            in_preferred = False
        # Inline marker: "Experience with X is a plus"
        if not in_preferred and re.search(r"\b(is a plus|a plus|nice to have|preferred|bonus points?)\b", stripped, re.I):
            preferred_text.append(stripped)
            continue
        (preferred_text if in_preferred else required_text).append(stripped)
    required = extract_skills("\n".join(required_text))
    preferred = [s for s in extract_skills("\n".join(preferred_text)) if s not in required]
    return required, preferred


def all_skill_names() -> list[str]:
    return sorted(TAXONOMY.keys(), key=str.lower)
