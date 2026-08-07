# Deployment Instructions

## What The Deployment Script Does

1. Loads environment variables from the specified .env file
2. Creates or updates Azure resources:
   - Resource Group
   - Azure Container Registry
   - App Service Plan
   - Web App
   - Application Insights
   - Log Analytics Workspace
3. Builds the Docker image locally using the specified Dockerfile and context
4. Pushes the image to Azure Container Registry
5. Configures the Web App with all environment variables

## Build and Test Locally with Docker

From the root directory, execute the following commands:

```bash
docker build -t mcdonalds-drive-thru-app -f ./app/Dockerfile ./app
docker run -p 8000:8000 --env-file ./app/backend/.env mcdonalds-drive-thru-app:latest
```

## Deploy the Application

After testing locally, deploy the application with:

```bash
./scripts/deploy.sh \
    --env-file ./app/backend/.env \
    --dockerfile ./app/Dockerfile \
    --context ./app \
    mcdonalds-drive-thru-assistant
```

## Enable EasyAuth (Entra ID Authentication) — Optional

Authentication is **off by default**. To restrict access to named individuals via
Microsoft Entra ID (Azure AD), follow the steps below.

### Prerequisites

1. An Entra ID App Registration (single-tenant recommended).
2. A client secret generated for that app registration.
3. The app must have a **Redirect URI** of
   `https://<YOUR_CONTAINER_APP_FQDN>/.auth/login/aad/callback`.

### Steps

```bash
# 1. Set the auth variables in azd env
azd env set AZURE_AUTH_ENABLED true
azd env set AZURE_AUTH_CLIENT_ID   <YOUR_APP_REGISTRATION_CLIENT_ID>
azd env set AZURE_AUTH_TENANT_ID   <YOUR_ENTRA_TENANT_ID>
azd env set AZURE_AUTH_CLIENT_SECRET <YOUR_CLIENT_SECRET_VALUE>

# 2. Re-deploy
azd up
```

### Restricting to Named Users Only

On the Enterprise Application (service principal) associated with your App
Registration, set:

```
Properties -> Assignment required? = Yes
```

(`appRoleAssignmentRequired = true` in Graph API terms.)

Then assign specific users or groups under **Users and groups**. Only assigned
principals will be able to sign in to the drive-thru app.

> ⚠️ **This requires an administrator to grant consent, and will lock you out
> if you are not one.** Requiring assignment disables *user* self-consent for
> the app, so the first sign-in fails with "Need admin approval — <app> needs
> permission to access resources in your organization that only an admin can
> grant", even for a user who has been assigned. Someone holding Application
> Administrator, Cloud Application Administrator or Global Administrator must
> run `az ad app permission admin-consent --id <app-id>` first. Global
> *Reader* is not sufficient — it is read-only.
>
> If you have no admin account to hand, leave assignment off. The app is
> registered single-tenant (`AzureADMyOrg`), so sign-in is still limited to
> members of your own tenant; you simply cannot narrow it to named
> individuals.

### How It Works

- `infra/core/security/container-app-auth.bicep` configures the Container App's
  built-in authentication (EasyAuth) to validate Entra ID tokens.
- The client secret is stored as a Container App secret named `aad-client-secret`
  and referenced by `clientSecretSettingName` — **the secret value never appears in
  any template or output**.
- The module is conditionally deployed: `enableAuth && !empty(authClientId)` both
  default to `false`, so a plain `azd up` from a fresh clone is unchanged.

### Caution

If you previously deployed with auth enabled and then unset the secret, the
Container App `secrets` array will be updated to remove `aad-client-secret`,
which can break an existing auth configuration. To cleanly disable auth, set
`AZURE_AUTH_ENABLED=false` and redeploy.
