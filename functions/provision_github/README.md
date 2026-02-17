# GitHub Provisioning Lambda - Security Model

## GitHub Organization Membership vs Repository Access

### Key Concept: Membership ≠ Repository Access

When you add someone as a GitHub organization member, they get:
- ✅ Can see the organization exists
- ✅ Can see other organization members
- ✅ Can see public repositories
- ❌ **CANNOT access private repositories** (secure default!)

### How Repository Access Works

```
Organization Member (this Lambda provisions this)
    ↓
    ├─ Option 1: Add to GitHub Team
    │   └─ Team has repo permissions
    │       └─ User inherits team's repo access
    │
    ├─ Option 2: Direct Collaborator
    │   └─ Add user directly to specific repo
    │       └─ User gets repo-specific permissions
    │
    └─ Option 3: Base Permissions (Org Setting)
        └─ Default: "No permission" (recommended)
        └─ Alternative: "Read" (dangerous - all members can read all repos)
```

---

## Security Architecture

### Phase 1: Organization Membership (This Lambda)
```python
# provision_github Lambda
invite_user_to_org(
    github_username="isaacbryant",
    role="direct_member",
    team_ids=[]  # Empty = no repo access
)

Result:
✅ isaac is now org member
❌ isaac has ZERO repo access
```

### Phase 2: Team Assignment (Separate Workflow)
```python
# Future: provision_github_teams Lambda
# Based on user role, add to appropriate teams

if role == "developer":
    add_to_teams(["developers", "frontend-team"])
    # developers team → access to internal repos
    # frontend-team → access to frontend repos

elif role == "contractor":
    add_to_teams(["contractors"])
    # contractors team → access to contractor-approved repos only

elif role == "admin":
    add_to_teams(["administrators"])
    # administrators team → access to all repos
```

---

## Example: Developer Onboarding Flow

### Step 1: Provision GitHub Org Membership
```json
{
  "user_id": "isaac",
  "email": "isaac@brightwings.io",
  "role": "developer",
  "github_username": "isaacbryant"
}
```

**Result:** isaac added to `brightwings` org as member (no repo access)

---

### Step 2: Add to Teams (Future Phase)
```json
{
  "user_id": "isaac",
  "github_username": "isaacbryant",
  "teams": [
    {
      "team_slug": "developers",
      "role": "member"
    },
    {
      "team_slug": "frontend-team",
      "role": "member"
    }
  ]
}
```

**Result:**
- isaac added to `developers` team
  - Inherits access to repos: `brightwings/api`, `brightwings/shared-lib`
- isaac added to `frontend-team`
  - Inherits access to repos: `brightwings/web-app`, `brightwings/mobile-app`

---

## GitHub Teams Structure (Example)

```
brightwings (Organization)
├── Teams:
│   ├── developers (team)
│   │   └── Repos: api (write), shared-lib (write), docs (write)
│   ├── contractors (team)
│   │   └── Repos: contractor-projects (write)
│   ├── frontend-team (team)
│   │   └── Repos: web-app (write), mobile-app (write)
│   └── administrators (team)
│       └── Repos: ALL (admin)
│
└── Members:
    ├── isaacbryant (member of: developers, frontend-team)
    ├── nmaulino (member of: developers)
    └── contractor-bob (member of: contractors)
```

---

## Security Benefits of This Approach

### 1. Least Privilege by Default
```
New user provisioned:
✅ Has org membership (can see org)
❌ Has NO repo access (cannot see code)

Must explicitly grant repo access via teams
→ Forces intentional permission grants
→ Prevents accidental exposure of sensitive repos
```

### 2. Separation of Concerns
```
provision_github Lambda:
- Handles org membership
- No repo permissions logic
- Simple, focused, auditable

provision_github_teams Lambda (future):
- Handles team assignments
- Repository access logic
- Role-based access control
```

### 3. Audit Trail
```
DynamoDB provisioning_state table:
{
  "user_id": "isaac",
  "system": "github",
  "github_username": "isaacbryant",
  "status": "active",
  "teams": ["developers", "frontend-team"],  # Added in Phase 2
  "repos_with_access": [  # Calculated from team permissions
    "brightwings/api",
    "brightwings/web-app"
  ]
}

Compliance question: "What repos can isaac access?"
→ Query DynamoDB (instant answer)
```

---

## GitHub API Reference

### Invite User to Organization
```bash
POST /orgs/{org}/invitations
{
  "invitee_id": 12345678,        # GitHub user ID
  "role": "direct_member",       # or "admin"
  "team_ids": []                 # Empty = no teams (secure)
}

Response: 201 Created
{
  "id": 1,
  "login": "isaacbryant",
  "role": "direct_member",
  "created_at": "2026-02-17T10:00:00Z"
}
```

### Add User to Team (Future Phase)
```bash
PUT /orgs/{org}/teams/{team_slug}/memberships/{username}
{
  "role": "member"  # or "maintainer"
}

Response: 200 OK
{
  "role": "member",
  "state": "active"
}
```

### Check User's Repository Access (Verification)
```bash
GET /repos/{owner}/{repo}/collaborators/{username}/permission

Response: 200 OK
{
  "permission": "write",  # or "read", "admin", "none"
  "role_name": "write"
}
```

---

## Testing Checklist

### Verify Secure Defaults:
- [ ] User invited to org successfully
- [ ] User is org member (can see org)
- [ ] User **cannot** access private repos
- [ ] User **cannot** clone private repos
- [ ] User **cannot** see private repo issues/PRs

### After Team Assignment (Phase 2):
- [ ] User can access repos assigned to their teams
- [ ] User **cannot** access repos not assigned to their teams
- [ ] Removing from team removes repo access

---

## Future Enhancements (Phase 2)

1. **provision_github_teams Lambda**
   - Add users to teams based on role
   - Handle team-based repository permissions

2. **GitHub RBAC Policy**
   - Define: Which roles get which teams
   - Example: `developer` → `developers` + `frontend-team`

3. **Repository Access Verification**
   - Query GitHub API to verify actual permissions
   - Compare against expected (drift detection)

4. **GitHub Enterprise SAML/SSO Integration**
   - Instead of invitations, use SAML provisioning
   - Auto-sync with corporate identity provider
