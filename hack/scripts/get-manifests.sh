#!/usr/bin/env bash
# KubeRay manifests are module-owned content. Committed snapshots live under
# opt/manifests/{odh,rhoai}/kuberay; this script materializes the selected
# snapshot at opt/manifests/kuberay for local tests and image builds.
# Network fetching is explicit with USE_LOCAL=false.
set -euo pipefail

PLATFORM_MANIFEST_DIR="${ODH_PLATFORM_TYPE:-OpenDataHub}"
if [[ "${PLATFORM_MANIFEST_DIR}" == "OpenDataHub" || "${PLATFORM_MANIFEST_DIR}" == "opendatahub" || "${PLATFORM_MANIFEST_DIR}" == "odh" ]]; then
    PLATFORM_MANIFEST_DIR="odh"
else
    PLATFORM_MANIFEST_DIR="rhoai"
fi
LOCAL_MANIFESTS_ROOT="${LOCAL_MANIFESTS_ROOT:-./opt/manifests}"
DST_MANIFESTS_DIR="${DST_MANIFESTS_DIR:-${LOCAL_MANIFESTS_ROOT}}"
USE_LOCAL="${USE_LOCAL:-true}"
MANIFESTS_SOURCES_FILE="${MANIFESTS_SOURCES_FILE:-./hack/manifests-sources.yaml}"
YQ="${YQ:-./bin/yq}"

TMP_DIR=$(mktemp -d -t "odh-kuberay-manifests.XXXXXXXXXX")
trap '{ rm -rf -- "${TMP_DIR}"; }' EXIT

function try_fetch_ref()
{
    local repo=$1
    local ref_type=$2  # "tags" or "heads"
    local ref=$3

    local git_ref="refs/${ref_type}/${ref}"
    local ref_name
    ref_name=$([[ ${ref_type} == "tags" ]] && echo "tag" || echo "branch")

    if git ls-remote --exit-code "${repo}" "${git_ref}" &>/dev/null; then
        if git fetch -q --depth 1 "${repo}" "${git_ref}" && git reset -q --hard FETCH_HEAD; then
            return 0
        else
            echo "ERROR: Failed to fetch ${ref_name} ${ref} from ${repo}"
            return 1
        fi
    fi
    return 1
}

function git_fetch_ref()
{
    local repo=$1
    local ref=$2
    local dir=$3

    mkdir -p "${dir}"
    pushd "${dir}" &>/dev/null
    git init -q

    # branch@sha or tag@sha format: fetch the specific commit directly
    if [[ ${ref} =~ ^([a-zA-Z0-9_./-]+)@([a-f0-9]{7,40})$ ]]; then
        local commit_sha="${BASH_REMATCH[2]}"

        git remote add origin "${repo}"
        if ! git fetch --depth 1 -q origin "${commit_sha}"; then
            echo "ERROR: Failed to fetch commit ${commit_sha} from ${repo}"
            popd &>/dev/null
            return 1
        fi
        if ! git reset -q --hard "${commit_sha}" 2>/dev/null; then
            echo "ERROR: Commit SHA ${commit_sha} not found in ${repo}"
            popd &>/dev/null
            return 1
        fi
    else
        # Plain branch, tag, or bare SHA: try tag then branch
        if try_fetch_ref "${repo}" "tags" "${ref}" || try_fetch_ref "${repo}" "heads" "${ref}"; then
            :  # success
        else
            echo "ERROR: '${ref}' is not a valid branch, tag, or commit SHA in ${repo}"
            popd &>/dev/null
            return 1
        fi
    fi

    popd &>/dev/null
}

download_manifest() {
    local key=$1
    local source_path="ray-operator/config"
    local repo_dir="${TMP_DIR}/${key}"

    if [[ "${USE_LOCAL}" == "true" ]]; then
        local snapshot_dir="${LOCAL_MANIFESTS_ROOT}/${PLATFORM_MANIFEST_DIR}/${key}"
        if [[ -d "${snapshot_dir}" ]]; then
            echo "Copying committed ${PLATFORM_MANIFEST_DIR} snapshot ..."
            rm -rf "${DST_MANIFESTS_DIR:?}/${key}"
            mkdir -p "${DST_MANIFESTS_DIR}/${key}"
            cp -a "${snapshot_dir}/." "${DST_MANIFESTS_DIR}/${key}/"
            return
        fi
        if [[ ! -e "../${key}/${source_path}" ]]; then
            echo "ERROR: local manifest snapshot not found at '${snapshot_dir}'"
            echo "       Run 'make update-manifests' from a connected environment"
            return 1
        fi
        echo "Copying from adjacent checkout ..."
        rm -rf "${DST_MANIFESTS_DIR:?}/${key}"
        mkdir -p "${DST_MANIFESTS_DIR}/${key}"
        cp -a "../${key}/${source_path}/." "${DST_MANIFESTS_DIR}/${key}/"
        return
    fi

    if ! command -v "${YQ}" >/dev/null 2>&1; then
        echo "ERROR: yq is required for network refreshes. Run 'make yq'." >&2
        return 1
    fi
    local repo_url repo_ref
    repo_url=$("${YQ}" -er ".components.${key}.${PLATFORM_MANIFEST_DIR}.repository" "${MANIFESTS_SOURCES_FILE}")
    repo_ref=$("${YQ}" -er ".components.${key}.${PLATFORM_MANIFEST_DIR}.ref" "${MANIFESTS_SOURCES_FILE}")
    source_path=$("${YQ}" -er ".components.${key}.sourcePath" "${MANIFESTS_SOURCES_FILE}")
    echo "Preparing ${key}: ${repo_url}@${repo_ref}"

    if ! git_fetch_ref "${repo_url}" "${repo_ref}" "${repo_dir}"; then
        echo "ERROR: Failed to fetch ref '${repo_ref}' from '${repo_url}' for component '${key}'"
        return 1
    fi

    rm -rf "${DST_MANIFESTS_DIR:?}/${key}"
    mkdir -p "${DST_MANIFESTS_DIR}/${key}"
    cp -a "${repo_dir}/${source_path}/." "${DST_MANIFESTS_DIR}/${key}/"
}

download_manifest kuberay

# Remove test/sample/scorecard artifacts not needed at runtime
rm -rf "${DST_MANIFESTS_DIR}"/*/e2e "${DST_MANIFESTS_DIR}"/*/scorecard \
       "${DST_MANIFESTS_DIR}"/*/test "${DST_MANIFESTS_DIR}"/*/samples \
       "${DST_MANIFESTS_DIR}"/*/example-*
find "${DST_MANIFESTS_DIR}" -name "README.md" -delete 2>/dev/null || true

echo "Manifests downloaded to ${DST_MANIFESTS_DIR}"
