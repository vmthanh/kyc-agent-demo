# Interview Rounds 2 and 3

## Round 2: culture + technical discussion

### Your positioning

You are a hands-on technical leader who has operated across research, product ML, distributed KYC services, and bank-wide AI platforms. Emphasize that you can go from whiteboard discovery to code, deployment, monitoring, and stakeholder adoption.

### Three STAR stories to prepare

1. **Shopee KYC at scale** — distributed task queue, 50M+ users, 100+ QPS. Focus on reliability, latency, regional integration, and fraud impact.
2. **Techcombank agentic architecture** — ambiguous multi-department needs translated into an extensible architecture. Focus on discovery, governance, and stakeholder alignment.
3. **Bank-wide ML platform/feature store** — reusable platform patterns across on-prem, AWS, and Databricks. Focus on standards without blocking delivery.

For each story, quantify the baseline, your personal decision, tradeoff, disagreement, measurable result, and what you would change.

### Culture themes from the JD

- ambiguity: explain your discovery method and how you make assumptions explicit;
- ownership: give an example of fixing an integration outside your formal scope;
- customer-facing clarity: show how you communicate options, risk, and milestones;
- speed with rigor: thin vertical slice first, then hardening by measured risk;
- reuse: distinguish reusable product capability from customer-specific adapter logic.

### Technical areas likely to be probed

- agent vs workflow and when not to use an agent;
- ontology design and domain knowledge capture;
- RAG quality, reranking, versioned policy, and citations;
- orchestration state, retries, idempotency, and long-running approvals;
- multi-tenancy, access control, audit, PII, and prompt injection;
- online/offline evaluation and incident debugging;
- translating prototype code into deployable customer components.

## Round 3: CEO — all aspects

### 90-second introduction

“I have spent about ten years building software and applied ML systems across Vietnam, Singapore, and Korea. At Shopee, I built KYC and face-recognition systems operating at large scale. At Techcombank, I now lead MLOps and design bank-wide ML and agentic platforms across AWS, Databricks, and on-prem environments. The common thread is turning a difficult domain problem into a reliable system that people adopt. This FDE role appeals to me because it combines hands-on engineering, domain discovery, and direct customer impact.”

### CEO-level messages

- You reduce the distance between customer pain and working software.
- You understand enterprise constraints without using them as excuses.
- You know which parts should become platform features and which remain adapters.
- You can represent the company credibly with executives, domain experts, and engineers.
- You measure value in customer outcomes, not demo novelty.

### Questions to ask the CEO

1. “Which customer workflow has created the clearest proof that cognitive ontologies outperform prompt-and-RAG approaches?”
2. “What distinguishes your strongest FDEs after their first six months?”
3. “How do you decide when a customer-specific pattern should become a platform capability?”
4. “Where do deployments most often stall today: ontology discovery, integration, evaluation, or organizational adoption?”
5. “What business outcome should this role materially improve in the first 90 days?”

### Avoid

- presenting yourself only as a manager;
- giving architecture without customer or business outcomes;
- claiming fully autonomous agents where human judgment is required;
- describing every tool you know instead of explaining decisions and tradeoffs;
- sharing confidential bank architecture, data, or metrics.
