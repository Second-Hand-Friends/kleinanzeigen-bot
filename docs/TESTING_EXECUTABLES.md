# Testing Executables from Pull Requests

This guide explains how to build and test executables from feature branches and pull requests.

## Manual Build Trigger

To test changes from a PR or feature branch:

1. **Navigate to Actions**:
   - Go to the GitHub repository
   - Click on the "Actions" tab

2. **Select Build Workflow**:
   - Find the "Build" workflow in the left sidebar
   - Click on it

3. **Run Workflow Manually**:
   - Click the "Run workflow" dropdown button
   - Select the branch you want to test (e.g., `feature/xdg-base-directories`)
   - Click "Run workflow"

## Downloading Artifacts

After the workflow completes:

1. **Find Your Workflow Run**:
   - Go back to the "Actions" tab
   - Click on the workflow run you just triggered

2. **Download Artifacts**:
   - Scroll down to the "Artifacts" section
   - Click on the artifact for your platform (e.g., `artifacts-windows-latest-feature-xdg-base-directories`)
   - Download the ZIP file

3. **Extract and Use**:
   - Extract the ZIP file
   - Follow the platform-specific instructions in the main README

## Platform-Specific Notes

### Windows
- Download `artifacts-windows-latest-<branch-name>.zip`
- Extract and run `kleinanzeigen-bot.exe`
- No additional permissions needed

### Linux
- Download `artifacts-ubuntu-latest-<branch-name>.zip`
- Make executable: `chmod +x kleinanzeigen-bot`
- Run: `./kleinanzeigen-bot --help`

### macOS
- Download `artifacts-macos-latest-<branch-name>.zip` (for Apple Silicon) or `artifacts-macos-15-intel-<branch-name>.zip` (for Intel)
- Make executable: `chmod +x kleinanzeigen-bot`
- Run: `./kleinanzeigen-bot --help`

## Troubleshooting

**Issue**: Workflow fails to start
- **Solution**: Ensure you have write permissions to the repository

**Issue**: Artifacts not appearing
- **Solution**: Check the workflow logs for errors during the upload step

**Issue**: Executable doesn't work
- **Solution**: Check the workflow logs for build errors and ensure you downloaded the correct platform artifact

## Artifact Naming Convention

- **Automatic builds** (main/release): `artifacts-{os}.zip`
- **Manual builds** (feature branches): `artifacts-{os}-{branch-name}.zip`

Examples:
- `artifacts-windows-latest.zip` (automatic build from main)
- `artifacts-windows-latest-feature-xdg-base-directories.zip` (manual build from feature branch)

## Permissions

Only repository members with write access can trigger manual workflow runs. This includes:
- Repository administrators
- Repository maintainers  
- Repository collaborators with write permissions

External contributors cannot trigger manual builds unless they have been granted write access.

## Cleanup

GitHub automatically cleans up artifacts after 90 days. You can also manually delete artifacts:
1. Go to the workflow run
2. Click on the artifact
3. Click "Delete artifact"

## Best Practices

1. **Test before merging**: Use this feature to test PR changes before merging
2. **Clean up**: Delete test artifacts when no longer needed
3. **Platform-specific testing**: Test on the platforms your users will use
4. **Document issues**: If you find bugs, document them in the PR discussion