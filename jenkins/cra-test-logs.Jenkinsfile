// Archives one hand-written test_logs.md for one release, so our CRA service can fetch it
// from a pinned build. Used by pod/cra-test-logs (shared with Team 24) and team22/cra-test-logs.
//
// Setup: in Jenkins, New Item -> Pipeline -> Pipeline section -> Definition "Pipeline script",
// paste this whole file, keep "Use Groovy Sandbox" ticked, Save. Build #1 only registers the
// parameters below and fails on purpose (RELEASE_VERSION is empty). From build #2 on, use
// "Build with Parameters". See docs/JENKINS_GETTING_STARTED.md.
//
// The file itself is not stored in Jenkins config. It is committed to a public GitHub repo
// and downloaded at an exact commit, so the archived bytes match a file anyone can check.
pipeline {
    agent any
    options {
        disableConcurrentBuilds()
        timeout(time: 5, unit: 'MINUTES')
    }
    parameters {
        string(name: 'PRODUCT_ID', defaultValue: 'prd-88',
               description: 'Product, e.g. prd-88')
        string(name: 'RELEASE_VERSION', defaultValue: '',
               description: 'Release, e.g. v1.5-jenkins')
        string(name: 'SOURCE_REPO', defaultValue: 'prithika-sundar/cra-artifact-test',
               description: 'Public GitHub repo (owner/name) holding artifacts/<product>/<release>/test_logs.md')
        string(name: 'SOURCE_COMMIT', defaultValue: '',
               description: 'Full 40-character commit SHA to download the file from')
        booleanParam(name: 'ARCHIVE_FILE', defaultValue: true,
                     description: 'Untick to make a build that archives nothing (the "-missing" test case)')
    }
    stages {
        stage('Check parameters') {
            steps {
                sh '''#!/bin/sh
                    set -eu
                    ok() { printf '%s' "$2" | grep -Eqx "$3" || { echo "Invalid $1: '$2'"; exit 1; }; }
                    ok PRODUCT_ID      "$PRODUCT_ID"      '[A-Za-z0-9][A-Za-z0-9._-]*'
                    ok RELEASE_VERSION "$RELEASE_VERSION" '[A-Za-z0-9][A-Za-z0-9._-]*'
                    ok SOURCE_REPO     "$SOURCE_REPO"     '[A-Za-z0-9._-]+/[A-Za-z0-9._-]+'
                    ok SOURCE_COMMIT   "$SOURCE_COMMIT"   '[0-9a-f]{40}'
                    case "$PRODUCT_ID/$RELEASE_VERSION" in *..*) echo "'..' not allowed"; exit 1;; esac
                '''
                script {
                    currentBuild.description = "${params.PRODUCT_ID}/${params.RELEASE_VERSION}" +
                        (params.ARCHIVE_FILE ? '' : ' (nothing archived)')
                }
            }
        }
        stage('Fetch and archive test_logs.md') {
            when { expression { return params.ARCHIVE_FILE } }
            steps {
                sh '''#!/bin/sh
                    set -eu
                    dest="artifacts/$PRODUCT_ID/$RELEASE_VERSION"
                    rm -rf artifacts
                    mkdir -p "$dest"
                    # Create the file first: some curl versions write nothing for a 0-byte body,
                    # and the "-empty" release must still archive an (empty) test_logs.md
                    : > "$dest/test_logs.md"
                    curl -fsSL --proto '=https' --retry 3 -o "$dest/test_logs.md" \
                        "https://raw.githubusercontent.com/$SOURCE_REPO/$SOURCE_COMMIT/$dest/test_logs.md"
                    echo "Archived bytes: $(wc -c < "$dest/test_logs.md")"
                    sha256sum "$dest/test_logs.md"
                '''
                archiveArtifacts artifacts: "artifacts/${params.PRODUCT_ID}/${params.RELEASE_VERSION}/test_logs.md",
                                 fingerprint: true
            }
        }
    }
}
