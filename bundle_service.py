from artifact_pipeline import retrieve_and_classify

def process_bundle(bundle):
    """
    Processes a bundle request.

    Person 2: retrieve and classify artifacts.
    Person 3: generate the final bundle and manifest.
    """

    # Person 2: fills in bundle["artifacts"] and bundle["manifest"]
    retrieve_and_classify(bundle)
    if bundle["status"] == "FAILED":
        return bundle

    # TODO: Person 3 - generate bundle and manifest

    return bundle