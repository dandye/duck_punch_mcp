
import os
import sys
import inspect
import pkgutil
import importlib
import json
import functools
import re
import typing
from typing import Any, Callable, Union, Optional, List, Dict

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Setup paths
# We are in src/duck_punch_mcp/gcp_server.py, so root is two levels up
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
gcp_packages_root = os.path.join(project_root, "external/google-cloud-python/packages")

# Whitelist of packages to expose
TARGET_PACKAGES = [
    # Original 4
    "google-cloud-access-approval",
    "google-cloud-asset",
    "google-cloud-advisorynotifications",
    "google-cloud-alloydb",

    # Batch 1 (10)
    "google-cloud-api-gateway",
    "google-cloud-api-keys",
    "google-cloud-apigee-connect",
    "google-cloud-apigee-registry",
    "google-cloud-apihub",
    "google-cloud-appengine-admin",
    "google-cloud-apphub",
    "google-cloud-artifact-registry",
    "google-cloud-assured-workloads",
    "google-cloud-automl",

    # Batch 2 (25)
    "google-cloud-backupdr",
    "google-cloud-bare-metal-solution",
    "google-cloud-batch",
    "google-cloud-beyondcorp-appconnections",
    "google-cloud-beyondcorp-appconnectors",
    "google-cloud-beyondcorp-appgateways",
    "google-cloud-beyondcorp-clientgateways",
    "google-cloud-biglake",
    "google-cloud-bigquery-analyticshub",
    "google-cloud-bigquery-connection",
    "google-cloud-bigquery-datapolicies",
    "google-cloud-bigquery-datatransfer",
    "google-cloud-bigquery-migration",
    "google-cloud-bigquery-reservation",
    "google-cloud-billing",
    "google-cloud-billing-budgets",
    "google-cloud-binary-authorization",
    "google-cloud-build",
    "google-cloud-certificate-manager",
    "google-cloud-channel",
    "google-cloud-cloudcontrolspartner",
    "google-cloud-commerce-consumer-procurement",
    "google-cloud-compute",
    "google-cloud-confidentialcomputing",
    "google-cloud-config",
]

# Add packages to sys.path
# We rely on installed packages (pip install) to handle namespaces correctly.
# Injecting source paths can break namespace packages if not done via pip install -e.
# for pkg_name in TARGET_PACKAGES:
#     pkg_path = os.path.join(gcp_packages_root, pkg_name)
#     if os.path.exists(pkg_path) and pkg_path not in sys.path:
#         sys.path.insert(0, pkg_path)

mcp = FastMCP("GoogleCloud")

# Global cache for clients
_clients = {}

def get_client(module_name: str, client_class_name: str):
    """Lazy loads and caches a GCP client."""
    key = f"{module_name}.{client_class_name}"
    if key in _clients:
        return _clients[key]

    try:
        module = importlib.import_module(module_name)
        client_cls = getattr(module, client_class_name)
        # Instantiate client. This assumes ADC (Application Default Credentials) work.
        # or GOOGLE_APPLICATION_CREDENTIALS is set.
        client = client_cls()
        _clients[key] = client
        return client
    except Exception as e:
        sys.stderr.write(f"Error instantiating {client_class_name}: {e}\n")
        raise

def is_simple_type(t):
    """Checks if a type is simple enough for MCP direct mapping."""
    if t in (str, int, float, bool, list, dict, type(None)):
        return True

    # Check for typing.Optional[Simple]
    origin = typing.get_origin(t)
    if origin is Union:
        args = typing.get_args(t)
        return all(is_simple_type(a) for a in args)

    return False

def create_wrapper(client_factory: Callable, method_name: str, method: Callable, tool_name: str):
    """Creates a wrapper for a GCP SDK method."""

    sig = inspect.signature(method)

    # Filter parameters
    new_params = []

    for name, p in sig.parameters.items():
        if name == 'self':
            continue

        # Determine annotation
        annotation = p.annotation

        # If no annotation, assume Any
        if annotation == inspect.Parameter.empty:
            annotation = Any

        # If it's not a simple type, force it to Dict (JSON object)
        # Using Any seems to cause Pydantic to try to introspect the original type sometimes?
        if not is_simple_type(annotation):
            # Fallback to dict for complex types (Protos usually serialize to dict)
            # or Any if we want to be safe. But let's try dict to break the Pydantic chain.
            annotation = dict

        # Handle default values
        # If default is not simple/JSON serializable, set to None
        default = p.default
        if default != inspect.Parameter.empty:
            try:
                # Basic check if it is serializable
                json.dumps(default)
            except (TypeError, OverflowError):
                default = None

        new_params.append(p.replace(annotation=annotation, default=default))

    # Do not use functools.wraps to avoid leaking original annotations/signature via __wrapped__
    def wrapper(*args, **kwargs):
        try:
            client = client_factory()
            func = getattr(client, method_name)

            # If the first argument is 'request' and it's a dict, the SDK usually handles it
            # if we pass it as a request object or keywords.
            # Let's just pass through.

            result = func(*args, **kwargs)

            # Result is often a Pager or a Proto message.
            # We need to serialize it.

            # Check if it's a Pager
            if hasattr(result, "pages"):
                # It's likely a pager. Let's return the first page or a list of items (limited)
                # For safety, let's convert to list of dicts (limited to first 20 items to avoid blowing up)
                items = []
                try:
                    for i, item in enumerate(result):
                        if i >= 20:
                            break
                        # ProtoMessage to dict
                        if hasattr(item, "__class__") and hasattr(item.__class__, "to_json"):
                             items.append(json.loads(item.__class__.to_json(item)))
                        elif hasattr(item, "__dict__"):
                             items.append(str(item)) # Fallback
                        else:
                             items.append(str(item))
                    return json.dumps(items, indent=2)
                except Exception as e:
                    return f"Error iterating pager: {e}"

            # Check if it's a Proto Message
            if hasattr(result, "__class__") and hasattr(result.__class__, "to_json"):
                return result.__class__.to_json(result)

            # Basic types
            if isinstance(result, (dict, list, str, int, float, bool, type(None))):
                if isinstance(result, (dict, list)):
                    return json.dumps(result, indent=2)
                return str(result)

            return str(result)

        except Exception as e:
            return f"Error executing {method_name}: {str(e)}"

    # Manually set attributes
    wrapper.__name__ = tool_name
    wrapper.__doc__ = method.__doc__

    # Update signature
    # Replace parameters AND return annotation
    new_sig = sig.replace(parameters=new_params, return_annotation=str)
    wrapper.__signature__ = new_sig

    # Explicitly set annotations based on new parameters
    wrapper.__annotations__ = {
        p.name: p.annotation for p in new_params
    }
    wrapper.__annotations__['return'] = str

    return wrapper

def pkg_to_prefix(pkg_name):
    # Remove google-cloud- prefix
    name = pkg_name.replace("google-cloud-", "")
    # Title case parts
    parts = name.split("-")
    return "".join(p.title() for p in parts)

def discover_tools():
    """Discover tools from whitelisted packages."""

    # Manual mapping of expected modules for the whitelisted packages
    # We could try to walk the directories, but let's be explicit for the 'first 2'

    candidates = [
        # Original 4
        ("google-cloud-access-approval", "google.cloud.accessapproval", "AccessApprovalClient"),
        ("google-cloud-asset", "google.cloud.asset_v1", "AssetServiceClient"),
        ("google-cloud-advisorynotifications", "google.cloud.advisorynotifications_v1", "AdvisoryNotificationsServiceClient"),
        ("google-cloud-alloydb", "google.cloud.alloydb_v1", "AlloyDBAdminClient"),

        # Batch 1 (10)
        ("google-cloud-api-gateway", "google.cloud.apigateway_v1", "ApiGatewayServiceClient"),
        ("google-cloud-api-keys", "google.cloud.api_keys_v2", "ApiKeysClient"),
        ("google-cloud-apigee-connect", "google.cloud.apigeeconnect_v1", "ConnectionServiceClient"),
        ("google-cloud-apigee-registry", "google.cloud.apigee_registry_v1", "RegistryClient"),
        ("google-cloud-apihub", "google.cloud.apihub_v1", "ApiHubClient"),
        ("google-cloud-appengine-admin", "google.cloud.appengine_admin_v1", "ApplicationsClient"),
        ("google-cloud-apphub", "google.cloud.apphub_v1", "AppHubClient"),
        ("google-cloud-artifact-registry", "google.cloud.artifactregistry_v1", "ArtifactRegistryClient"),
        ("google-cloud-assured-workloads", "google.cloud.assuredworkloads_v1", "AssuredWorkloadsServiceClient"),
        ("google-cloud-automl", "google.cloud.automl_v1", "AutoMlClient"),

        # Batch 2 (25)
        ("google-cloud-backupdr", "google.cloud.backupdr_v1", "BackupDRClient"),
        ("google-cloud-bare-metal-solution", "google.cloud.bare_metal_solution_v2", "BareMetalSolutionClient"),
        ("google-cloud-batch", "google.cloud.batch_v1", "BatchServiceClient"),
        ("google-cloud-beyondcorp-appconnections", "google.cloud.beyondcorp_appconnections_v1", "AppConnectionsServiceClient"),
        ("google-cloud-beyondcorp-appconnectors", "google.cloud.beyondcorp_appconnectors_v1", "AppConnectorsServiceClient"),
        ("google-cloud-beyondcorp-appgateways", "google.cloud.beyondcorp_appgateways_v1", "AppGatewaysServiceClient"),
        ("google-cloud-beyondcorp-clientgateways", "google.cloud.beyondcorp_clientgateways_v1", "ClientGatewaysServiceClient"),
        ("google-cloud-biglake", "google.cloud.biglake_v1", "IcebergCatalogServiceClient"),
        ("google-cloud-bigquery-analyticshub", "google.cloud.bigquery_analyticshub_v1", "AnalyticsHubServiceClient"),
        ("google-cloud-bigquery-connection", "google.cloud.bigquery_connection_v1", "ConnectionServiceClient"),
        ("google-cloud-bigquery-datapolicies", "google.cloud.bigquery_datapolicies_v1", "DataPolicyServiceClient"),
        ("google-cloud-bigquery-datatransfer", "google.cloud.bigquery_datatransfer_v1", "DataTransferServiceClient"),
        ("google-cloud-bigquery-migration", "google.cloud.bigquery_migration_v2", "MigrationServiceClient"),
        ("google-cloud-bigquery-reservation", "google.cloud.bigquery_reservation_v1", "ReservationServiceClient"),
        ("google-cloud-billing", "google.cloud.billing_v1", "CloudBillingClient"),
        ("google-cloud-billing-budgets", "google.cloud.billing.budgets_v1", "BudgetServiceClient"),
        ("google-cloud-binary-authorization", "google.cloud.binaryauthorization_v1", "BinauthzManagementServiceV1Client"),
        ("google-cloud-build", "google.cloud.devtools.cloudbuild_v1", "CloudBuildClient"),
        ("google-cloud-certificate-manager", "google.cloud.certificate_manager_v1", "CertificateManagerClient"),
        ("google-cloud-channel", "google.cloud.channel_v1", "CloudChannelServiceClient"),
        ("google-cloud-cloudcontrolspartner", "google.cloud.cloudcontrolspartner_v1", "CloudControlsPartnerCoreClient"),
        ("google-cloud-commerce-consumer-procurement", "google.cloud.commerce_consumer_procurement_v1", "ConsumerProcurementServiceClient"),
        ("google-cloud-compute", "google.cloud.compute_v1", "InstancesClient"),
        ("google-cloud-confidentialcomputing", "google.cloud.confidentialcomputing_v1", "ConfidentialComputingClient"),
        ("google-cloud-config", "google.cloud.config_v1", "ConfigClient"),

    # Batch 3 (Automatic)
("google-ads-admanager", "google.ads.admanager_v1", "PrivateAuctionDealServiceClient"),
    ("google-ads-datamanager", "google.ads.datamanager_v1", "IngestionServiceClient"),
    ("google-ai-generativelanguage", "google.ai.generativelanguage_v1beta", "CacheServiceClient"),
    ("google-analytics-admin", "google.analytics.admin_v1beta", "AnalyticsAdminServiceClient"),
    ("google-analytics-data", "google.analytics.data_v1beta", "BetaAnalyticsDataClient"),
    ("google-apps-chat", "google.apps.chat_v1", "ChatServiceClient"),
    ("google-apps-events-subscriptions", "google.apps.events_subscriptions_v1beta", "SubscriptionsServiceClient"),
    ("google-apps-meet", "google.apps.meet_v2", "ConferenceRecordsServiceClient"),
    ("google-area120-tables", "google.area120.tables_v1alpha1", "TablesServiceClient"),
    ("google-cloud-bigquery-biglake", "google.cloud.bigquery_biglake_v1alpha1", "MetastoreServiceClient"),
    ("google-cloud-bigquery-data-exchange", "google.cloud.bigquery_data_exchange_v1beta1", "AnalyticsHubServiceClient"),
    ("google-cloud-bigquery-storage", "google.cloud.bigquery_storage_v1alpha", "MetastorePartitionServiceClient"),
    ("google-cloud-capacityplanner", "google.cloud.capacityplanner_v1beta", "UsageServiceClient"),
    ("google-cloud-chronicle", "google.cloud.chronicle_v1", "DataAccessControlServiceClient"),
    ("google-cloud-cloudsecuritycompliance", "google.cloud.cloudsecuritycompliance_v1", "ConfigClient"),
    ("google-cloud-compute-v1beta", "google.cloud.compute_v1beta", "RegionTargetTcpProxiesClient"),
    ("google-cloud-configdelivery", "google.cloud.configdelivery_v1beta", "ConfigDeliveryClient"),
    ("google-cloud-contact-center-insights", "google.cloud.contact_center_insights_v1", "ContactCenterInsightsClient"),
    ("google-cloud-container", "google.cloud.container_v1beta1", "ClusterManagerClient"),
    ("google-cloud-containeranalysis", "google.cloud.devtools", "ContainerAnalysisClient"),
    ("google-cloud-contentwarehouse", "google.cloud.contentwarehouse_v1", "RuleSetServiceClient"),
    ("google-cloud-data-fusion", "google.cloud.data_fusion_v1", "DataFusionClient"),
    ("google-cloud-data-qna", "google.cloud.dataqna_v1alpha", "QuestionServiceClient"),
    ("google-cloud-databasecenter", "google.cloud.databasecenter_v1beta", "DatabaseCenterClient"),
    ("google-cloud-datacatalog", "google.cloud.datacatalog_v1beta1", "DataCatalogClient"),
    ("google-cloud-datacatalog-lineage", "google.cloud.datacatalog_lineage_v1", "LineageClient"),
    ("google-cloud-dataflow-client", "google.cloud.dataflow_v1beta3", "SnapshotsV1Beta3Client"),
    ("google-cloud-dataform", "google.cloud.dataform_v1", "DataformClient"),
    ("google-cloud-datalabeling", "google.cloud.datalabeling_v1beta1", "DataLabelingServiceClient"),
    ("google-cloud-dataplex", "google.cloud.dataplex_v1", "MetadataServiceClient"),
    ("google-cloud-dataproc", "google.cloud.dataproc_v1", "AutoscalingPolicyServiceClient"),
    ("google-cloud-dataproc-metastore", "google.cloud.metastore_v1", "DataprocMetastoreClient"),
    ("google-cloud-datastream", "google.cloud.datastream_v1alpha1", "DatastreamClient"),
    ("google-cloud-deploy", "google.cloud.deploy_v1", "CloudDeployClient"),
    ("google-cloud-developerconnect", "google.cloud.developerconnect_v1", "DeveloperConnectClient"),
    ("google-cloud-devicestreaming", "google.cloud.devicestreaming_v1", "DirectAccessServiceClient"),
    ("google-cloud-dialogflow", "google.cloud.dialogflow_v2beta1", "AgentsClient"),
    ("google-cloud-dialogflow-cx", "google.cloud.dialogflowcx_v3beta1", "PagesClient"),
    ("google-cloud-discoveryengine", "google.cloud.discoveryengine_v1beta", "RankServiceClient"),
    ("google-cloud-dlp", "google.cloud.dlp_v2", "DlpServiceClient"),
    ("google-cloud-dms", "google.cloud.clouddms_v1", "DataMigrationServiceClient"),
    ("google-cloud-documentai", "google.cloud.documentai_v1beta3", "DocumentServiceClient"),
    ("google-cloud-domains", "google.cloud.domains_v1beta1", "DomainsClient"),
    ("google-cloud-edgecontainer", "google.cloud.edgecontainer_v1", "EdgeContainerClient"),
    ("google-cloud-edgenetwork", "google.cloud.edgenetwork_v1", "EdgeNetworkClient"),
    ("google-cloud-essential-contacts", "google.cloud.essential_contacts_v1", "EssentialContactsServiceClient"),
    ("google-cloud-eventarc", "google.cloud.eventarc_v1", "EventarcClient"),
    ("google-cloud-eventarc-publishing", "google.cloud.eventarc_publishing_v1", "PublisherClient"),
    ("google-cloud-filestore", "google.cloud.filestore_v1", "CloudFilestoreManagerClient"),
    ("google-cloud-financialservices", "google.cloud.financialservices_v1", "AMLClient"),
    ("google-cloud-functions", "google.cloud.functions_v1", "CloudFunctionsServiceClient"),
    ("google-cloud-gdchardwaremanagement", "google.cloud.gdchardwaremanagement_v1alpha", "GDCHardwareManagementClient"),
    ("google-cloud-geminidataanalytics", "google.cloud.geminidataanalytics_v1alpha", "DataChatServiceClient"),
    ("google-cloud-gke-backup", "google.cloud.gke_backup_v1", "BackupForGKEClient"),
    ("google-cloud-gke-connect-gateway", "google.cloud.gkeconnect", "GatewayControlClient"),
    ("google-cloud-gke-hub", "google.cloud.gkehub_v1", "GkeHubClient"),
    ("google-cloud-gke-multicloud", "google.cloud.gke_multicloud_v1", "AzureClustersClient"),
    ("google-cloud-gkerecommender", "google.cloud.gkerecommender_v1", "GkeInferenceQuickstartClient"),
    ("google-cloud-gsuiteaddons", "google.cloud.gsuiteaddons_v1", "GSuiteAddOnsClient"),
    ("google-cloud-hypercomputecluster", "google.cloud.hypercomputecluster_v1beta", "HypercomputeClusterClient"),
    ("google-cloud-iam", "google.cloud.iam_admin_v1", "IAMClient"),
    ("google-cloud-ids", "google.cloud.ids_v1", "IDSClient"),
    ("google-cloud-kms", "google.cloud.kms_v1", "EkmServiceClient"),
    ("google-cloud-kms-inventory", "google.cloud.kms_inventory_v1", "KeyTrackingServiceClient"),
    ("google-cloud-language", "google.cloud.language_v2", "LanguageServiceClient"),
    ("google-cloud-licensemanager", "google.cloud.licensemanager_v1", "LicenseManagerClient"),
    ("google-cloud-life-sciences", "google.cloud.lifesciences_v2beta", "WorkflowsServiceV2BetaClient"),
    ("google-cloud-locationfinder", "google.cloud.locationfinder_v1", "CloudLocationFinderClient"),
    ("google-cloud-lustre", "google.cloud.lustre_v1", "LustreClient"),
    ("google-cloud-maintenance-api", "google.cloud.maintenance_api_v1beta", "MaintenanceClient"),
    ("google-cloud-managed-identities", "google.cloud.managedidentities_v1", "ManagedIdentitiesServiceClient"),
    ("google-cloud-managedkafka", "google.cloud.managedkafka_v1", "ManagedKafkaClient"),
    ("google-cloud-managedkafka-schemaregistry", "google.cloud.managedkafka_schemaregistry_v1", "ManagedSchemaRegistryClient"),
    ("google-cloud-media-translation", "google.cloud.mediatranslation_v1beta1", "SpeechTranslationServiceClient"),
    ("google-cloud-memcache", "google.cloud.memcache_v1beta2", "CloudMemcacheClient"),
    ("google-cloud-memorystore", "google.cloud.memorystore_v1", "MemorystoreClient"),
    ("google-cloud-migrationcenter", "google.cloud.migrationcenter_v1", "MigrationCenterClient"),
    ("google-cloud-modelarmor", "google.cloud.modelarmor_v1beta", "ModelArmorClient"),
    ("google-cloud-monitoring", "google.cloud.monitoring_v3", "GroupServiceClient"),
    ("google-cloud-monitoring-dashboards", "google.cloud.monitoring_dashboard_v1", "DashboardsServiceClient"),
    ("google-cloud-monitoring-metrics-scopes", "google.cloud.monitoring_metrics_scope_v1", "MetricsScopesClient"),
    ("google-cloud-netapp", "google.cloud.netapp_v1", "NetAppClient"),
    ("google-cloud-network-connectivity", "google.cloud.networkconnectivity_v1alpha1", "HubServiceClient"),
    ("google-cloud-network-management", "google.cloud.network_management_v1", "ReachabilityServiceClient"),
    ("google-cloud-network-security", "google.cloud.network_security_v1alpha1", "MirroringClient"),
    ("google-cloud-network-services", "google.cloud.network_services_v1", "DepServiceClient"),
    ("google-cloud-notebooks", "google.cloud.notebooks_v1", "ManagedNotebookServiceClient"),
    ("google-cloud-optimization", "google.cloud.optimization_v1", "FleetRoutingClient"),
    ("google-cloud-oracledatabase", "google.cloud.oracledatabase_v1", "OracleDatabaseClient"),
    ("google-cloud-orchestration-airflow", "google.cloud.orchestration", "ImageVersionsClient"),
    ("google-cloud-org-policy", "google.cloud.orgpolicy_v2", "OrgPolicyClient"),
    ("google-cloud-os-config", "google.cloud.osconfig_v1", "OsConfigServiceClient"),
    ("google-cloud-os-login", "google.cloud.oslogin_v1", "OsLoginServiceClient"),
    ("google-cloud-parallelstore", "google.cloud.parallelstore_v1beta", "ParallelstoreClient"),
    ("google-cloud-parametermanager", "google.cloud.parametermanager_v1", "ParameterManagerClient"),
    ("google-cloud-policy-troubleshooter", "google.cloud.policytroubleshooter_v1", "IamCheckerClient"),
    ("google-cloud-policysimulator", "google.cloud.policysimulator_v1", "SimulatorClient"),
    ("google-cloud-policytroubleshooter-iam", "google.cloud.policytroubleshooter_iam_v3", "PolicyTroubleshooterClient"),
    ("google-cloud-private-catalog", "google.cloud.privatecatalog_v1beta1", "PrivateCatalogClient"),
    ("google-cloud-privilegedaccessmanager", "google.cloud.privilegedaccessmanager_v1", "PrivilegedAccessManagerClient"),
    ("google-cloud-quotas", "google.cloud.cloudquotas_v1", "CloudQuotasClient"),
    ("google-cloud-rapidmigrationassessment", "google.cloud.rapidmigrationassessment_v1", "RapidMigrationAssessmentClient"),
    ("google-cloud-recaptcha-enterprise", "google.cloud.recaptchaenterprise_v1", "RecaptchaEnterpriseServiceClient"),
    ("google-cloud-recommendations-ai", "google.cloud.recommendationengine_v1beta1", "PredictionApiKeyRegistryClient"),
    ("google-cloud-recommender", "google.cloud.recommender_v1beta1", "RecommenderClient"),
    ("google-cloud-redis", "google.cloud.redis_v1beta1", "CloudRedisClient"),
    ("google-cloud-redis-cluster", "google.cloud.redis_cluster_v1beta1", "CloudRedisClusterClient"),
    ("google-cloud-resource-manager", "google.cloud.resourcemanager_v3", "TagHoldsClient"),
    ("google-cloud-retail", "google.cloud.retail_v2", "ModelServiceClient"),
    ("google-cloud-run", "google.cloud.run_v2", "BuildsClient"),
    ("google-cloud-saasplatform-saasservicemgmt", "google.cloud.saasplatform_saasservicemgmt_v1beta1", "SaasDeploymentsClient"),
    ("google-cloud-scheduler", "google.cloud.scheduler_v1beta1", "CloudSchedulerClient"),
    ("google-cloud-secret-manager", "google.cloud.secretmanager_v1", "SecretManagerServiceClient"),
    ("google-cloud-securesourcemanager", "google.cloud.securesourcemanager_v1", "SecureSourceManagerClient"),
    ("google-cloud-securitycenter", "google.cloud.securitycenter_v1p1beta1", "SecurityCenterClient"),
    ("google-cloud-securitycentermanagement", "google.cloud.securitycentermanagement_v1", "SecurityCenterManagementClient"),
    ("google-cloud-service-control", "google.cloud.servicecontrol_v1", "QuotaControllerClient"),
    ("google-cloud-service-directory", "google.cloud.servicedirectory_v1beta1", "LookupServiceClient"),
    ("google-cloud-service-management", "google.cloud.servicemanagement_v1", "ServiceManagerClient"),
    ("google-cloud-service-usage", "google.cloud.service_usage_v1", "ServiceUsageClient"),
    ("google-cloud-servicehealth", "google.cloud.servicehealth_v1", "ServiceHealthClient"),
    ("google-cloud-shell", "google.cloud.shell_v1", "CloudShellServiceClient"),
    ("google-cloud-speech", "google.cloud.speech_v1", "SpeechClient"),
    ("google-cloud-storage-control", "google.cloud.storage_control_v2", "StorageControlClient"),
    ("google-cloud-storage-transfer", "google.cloud.storage_transfer_v1", "StorageTransferServiceClient"),
    ("google-cloud-storagebatchoperations", "google.cloud.storagebatchoperations_v1", "StorageBatchOperationsClient"),
    ("google-cloud-storageinsights", "google.cloud.storageinsights_v1", "StorageInsightsClient"),
    ("google-cloud-support", "google.cloud.support_v2beta", "FeedServiceClient"),
    ("google-cloud-talent", "google.cloud.talent_v4beta1", "CompletionClient"),
    ("google-cloud-tasks", "google.cloud.tasks_v2", "CloudTasksClient"),
    ("google-cloud-telcoautomation", "google.cloud.telcoautomation_v1alpha1", "TelcoAutomationClient"),
    ("google-cloud-texttospeech", "google.cloud.texttospeech_v1beta1", "TextToSpeechClient"),
    ("google-cloud-tpu", "google.cloud.tpu_v2", "TpuClient"),
    ("google-cloud-trace", "google.cloud.trace_v2", "TraceServiceClient"),
    ("google-cloud-translate", "google.cloud.translate_v3", "TranslationServiceClient"),
    ("google-cloud-vectorsearch", "google.cloud.vectorsearch_v1beta", "DataObjectServiceClient"),
    ("google-cloud-video-live-stream", "google.cloud.video", "LivestreamServiceClient"),
    ("google-cloud-video-stitcher", "google.cloud.video", "VideoStitcherServiceClient"),
    ("google-cloud-video-transcoder", "google.cloud.video", "TranscoderServiceClient"),
    ("google-cloud-videointelligence", "google.cloud.videointelligence_v1p3beta1", "VideoIntelligenceServiceClient"),
    ("google-cloud-vision", "google.cloud.vision_v1p1beta1", "ImageAnnotatorClient"),
    ("google-cloud-visionai", "google.cloud.visionai_v1alpha1", "StreamingServiceClient"),
    ("google-cloud-vm-migration", "google.cloud.vmmigration_v1", "VmMigrationClient"),
    ("google-cloud-vmwareengine", "google.cloud.vmwareengine_v1", "VmwareEngineClient"),
    ("google-cloud-vpc-access", "google.cloud.vpcaccess_v1", "VpcAccessServiceClient"),
    ("google-cloud-webrisk", "google.cloud.webrisk_v1beta1", "WebRiskServiceV1Beta1Client"),
    ("google-cloud-websecurityscanner", "google.cloud.websecurityscanner_v1", "WebSecurityScannerClient"),
    ("google-cloud-workflows", "google.cloud.workflows_v1", "WorkflowsClient"),
    ("google-cloud-workstations", "google.cloud.workstations_v1", "WorkstationsClient"),
    ("google-maps-addressvalidation", "google.maps.addressvalidation_v1", "AddressValidationClient"),
    ("google-maps-areainsights", "google.maps.areainsights_v1", "AreaInsightsClient"),
    ("google-maps-fleetengine", "google.maps.fleetengine_v1", "VehicleServiceClient"),
    ("google-maps-fleetengine-delivery", "google.maps.fleetengine_delivery_v1", "DeliveryServiceClient"),
    ("google-maps-mapsplatformdatasets", "google.maps.mapsplatformdatasets_v1", "MapsPlatformDatasetsClient"),
    ("google-maps-places", "google.maps.places_v1", "PlacesClient"),
    ("google-maps-routeoptimization", "google.maps.routeoptimization_v1", "RouteOptimizationClient"),
    ("google-maps-routing", "google.maps.routing_v2", "RoutesClient"),
    ("google-maps-solar", "google.maps.solar_v1", "SolarClient"),
    ("google-shopping-css", "google.shopping.css_v1", "CssProductsServiceClient"),
    ("google-shopping-merchant-accounts", "google.shopping.merchant_accounts_v1beta", "OnlineReturnPolicyServiceClient"),
    ("google-shopping-merchant-conversions", "google.shopping.merchant_conversions_v1beta", "ConversionSourcesServiceClient"),
    ("google-shopping-merchant-datasources", "google.shopping.merchant_datasources_v1beta", "DataSourcesServiceClient"),
    ("google-shopping-merchant-inventories", "google.shopping.merchant_inventories_v1beta", "RegionalInventoryServiceClient"),
    ("google-shopping-merchant-issueresolution", "google.shopping.merchant_issueresolution_v1", "IssueResolutionServiceClient"),
    ("google-shopping-merchant-lfp", "google.shopping.merchant_lfp_v1beta", "LfpSaleServiceClient"),
    ("google-shopping-merchant-notifications", "google.shopping.merchant_notifications_v1beta", "NotificationsApiServiceClient"),
    ("google-shopping-merchant-products", "google.shopping.merchant_products_v1", "ProductsServiceClient"),
    ("google-shopping-merchant-productstudio", "google.shopping.merchant_productstudio_v1alpha", "TextSuggestionsServiceClient"),
    ("google-shopping-merchant-promotions", "google.shopping.merchant_promotions_v1", "PromotionsServiceClient"),
    ("google-shopping-merchant-quota", "google.shopping.merchant_quota_v1beta", "QuotaServiceClient"),
    ("google-shopping-merchant-reports", "google.shopping.merchant_reports_v1alpha", "ReportServiceClient"),
    ("google-shopping-merchant-reviews", "google.shopping.merchant_reviews_v1beta", "ProductReviewsServiceClient"),]

    for pkg_name, module_name, client_name in candidates:
        try:
            mod = importlib.import_module(module_name)

            # Find the client class
            # We accept exact match or something ending with the name
            client_cls = getattr(mod, client_name, None)
            if not client_cls:
                # Try fallback for v1alpha/beta naming inconsistencies if needed
                # But our list is pretty specific.
                sys.stderr.write(f"Could not find {client_name} in {module_name}\n")
                continue

            print(f"Registering tools for {client_name} ({pkg_name})...")

            # Factory to get instance
            def make_factory(m_name, c_name):
                return lambda: get_client(m_name, c_name)

            client_factory = make_factory(module_name, client_name)

            # Use pkg name to generate prefix
            # e.g. google-cloud-bigquery-connection -> BigQueryConnection
            pkg_prefix = pkg_to_prefix(pkg_name)

            # Iterate over methods
            for name, method in inspect.getmembers(client_cls):
                if name.startswith("_"): continue
                if not inspect.isfunction(method): continue

                # Filter out some common non-API methods
                if name in ["from_service_account_file", "from_service_account_info", "from_service_account_json", "get_mtls_endpoint_and_cert_source", "parse_common_billing_account_path", "parse_common_folder_path", "parse_common_location_path", "parse_common_organization_path", "parse_common_project_path", "common_billing_account_path", "common_folder_path", "common_location_path", "common_organization_path", "common_project_path"]:
                    continue

                # Tool name: <PkgPrefix>_<Method>
                # e.g. BigQueryConnection_list_connections
                tool_name = f"{pkg_prefix}_{name}"

                try:
                    wrapper = create_wrapper(client_factory, name, method, tool_name)
                    mcp.add_tool(wrapper)
                except Exception as e:
                    # Ignore duplicates if they happen (though prefixes should handle it)
                    if "already exists" in str(e):
                        sys.stderr.write(f"Tool already exists: {tool_name}\n")
                    else:
                        sys.stderr.write(f"Failed to wrap {name}: {e}\n")

        except ImportError as e:
            sys.stderr.write(f"Failed to import {module_name}: {e}\n")

if __name__ == "__main__":
    discover_tools()
    mcp.run()
