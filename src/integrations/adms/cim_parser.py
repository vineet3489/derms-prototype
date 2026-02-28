"""
CIM XML Parser for GE ADMS Integration
Parses IEC 61970/61968 CIM network model XML into DERMS internal models.
In production this connects to GE ADMS via IEC 61968 Message Bus or APM REST API.
"""
from lxml import etree
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# CIM XML namespaces
CIM_NS = {
    "cim": "http://iec.ch/TC57/2013/CIM-schema-cim16#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "md":  "http://iec.ch/TC57/61970-552/ModelDescription/1#",
}


@dataclass
class CIMSubstation:
    rdf_id: str
    name: str
    description: str = ""


@dataclass
class CIMBaseVoltage:
    rdf_id: str
    nominal_voltage: float  # kV


@dataclass
class CIMFeeder:
    rdf_id: str
    name: str
    substation_id: str
    voltage_level_id: str
    rated_mva: float = 10.0


@dataclass
class CIMPowerTransformer:
    rdf_id: str
    name: str
    equipment_container_id: str   # Feeder or VoltageLevel
    rated_kva: float = 250.0
    rated_voltage_hv: float = 11.0   # kV
    rated_voltage_lv: float = 0.415  # kV


@dataclass
class CIMEnergyConsumer:
    rdf_id: str
    name: str
    equipment_container_id: str
    p_fixed_w: float = 0.0


@dataclass
class CIMNetworkModel:
    substations: Dict[str, CIMSubstation] = field(default_factory=dict)
    base_voltages: Dict[str, CIMBaseVoltage] = field(default_factory=dict)
    feeders: Dict[str, CIMFeeder] = field(default_factory=dict)
    transformers: Dict[str, CIMPowerTransformer] = field(default_factory=dict)
    consumers: Dict[str, CIMEnergyConsumer] = field(default_factory=dict)


def parse_cim_xml(xml_content: str) -> CIMNetworkModel:
    """
    Parse CIM XML string (IEC 61970-552 format) into CIMNetworkModel.
    Handles GE ADMS CIM export format.
    """
    model = CIMNetworkModel()

    try:
        root = etree.fromstring(xml_content.encode("utf-8"))
    except etree.XMLSyntaxError as e:
        logger.error(f"CIM XML parse error: {e}")
        return model

    def get_rdf_id(elem) -> str:
        return elem.get(f"{{{CIM_NS['rdf']}}}ID", "")

    def get_resource_ref(elem, tag: str) -> str:
        child = elem.find(f"cim:{tag}", CIM_NS)
        if child is not None:
            ref = child.get(f"{{{CIM_NS['rdf']}}}resource", "")
            return ref.lstrip("#")
        return ""

    def get_text(elem, tag: str) -> str:
        child = elem.find(f"cim:{tag}", CIM_NS)
        return child.text.strip() if child is not None and child.text else ""

    def get_float(elem, tag: str, default: float = 0.0) -> float:
        text = get_text(elem, tag)
        try:
            return float(text)
        except (ValueError, TypeError):
            return default

    # Parse Substations
    for subst in root.findall("cim:Substation", CIM_NS):
        rdf_id = get_rdf_id(subst)
        name = get_text(subst, "IdentifiedObject.name")
        desc = get_text(subst, "IdentifiedObject.description")
        model.substations[rdf_id] = CIMSubstation(rdf_id=rdf_id, name=name, description=desc)

    # Parse BaseVoltages
    for bv in root.findall("cim:BaseVoltage", CIM_NS):
        rdf_id = get_rdf_id(bv)
        voltage = get_float(bv, "BaseVoltage.nominalVoltage", 11.0)
        model.base_voltages[rdf_id] = CIMBaseVoltage(rdf_id=rdf_id, nominal_voltage=voltage)

    # Parse Feeders (Line/Feeder containers)
    for feeder in root.findall("cim:Feeder", CIM_NS):
        rdf_id = get_rdf_id(feeder)
        name = get_text(feeder, "IdentifiedObject.name")
        subst_id = get_resource_ref(feeder, "Feeder.NormalEnergizingSubstation")
        model.feeders[rdf_id] = CIMFeeder(
            rdf_id=rdf_id, name=name,
            substation_id=subst_id, voltage_level_id=""
        )

    # Parse PowerTransformers (Distribution Transformers)
    for pt in root.findall("cim:PowerTransformer", CIM_NS):
        rdf_id = get_rdf_id(pt)
        name = get_text(pt, "IdentifiedObject.name")
        container_id = get_resource_ref(pt, "Equipment.EquipmentContainer")
        model.transformers[rdf_id] = CIMPowerTransformer(
            rdf_id=rdf_id, name=name,
            equipment_container_id=container_id
        )

    # Parse PowerTransformerEnds for ratings
    for pte in root.findall("cim:PowerTransformerEnd", CIM_NS):
        pt_id = get_resource_ref(pte, "PowerTransformerEnd.PowerTransformer")
        rated_s = get_float(pte, "PowerTransformerEnd.ratedS", 250.0)
        rated_u = get_float(pte, "PowerTransformerEnd.ratedU", 11000.0)
        if pt_id in model.transformers:
            if rated_u > 1000:  # HV side
                model.transformers[pt_id].rated_kva = rated_s
                model.transformers[pt_id].rated_voltage_hv = rated_u / 1000
            else:
                model.transformers[pt_id].rated_voltage_lv = rated_u / 1000

    # Parse EnergyConsumers
    for ec in root.findall("cim:EnergyConsumer", CIM_NS):
        rdf_id = get_rdf_id(ec)
        name = get_text(ec, "IdentifiedObject.name")
        container_id = get_resource_ref(ec, "Equipment.EquipmentContainer")
        p_fixed = get_float(ec, "EnergyConsumer.pfixed", 0.0)
        model.consumers[rdf_id] = CIMEnergyConsumer(
            rdf_id=rdf_id, name=name,
            equipment_container_id=container_id,
            p_fixed_w=p_fixed
        )

    logger.info(
        f"CIM parsed: {len(model.substations)} substations, "
        f"{len(model.feeders)} feeders, "
        f"{len(model.transformers)} transformers, "
        f"{len(model.consumers)} consumers"
    )
    return model


def cim_model_to_derms(cim: CIMNetworkModel) -> dict:
    """Convert CIM network model to DERMS internal topology dict."""
    result = {
        "substations": [],
        "feeders": [],
        "distribution_transformers": [],
    }

    for rdf_id, subst in cim.substations.items():
        result["substations"].append({
            "cim_id": rdf_id,
            "name": subst.name,
        })

    for rdf_id, feeder in cim.feeders.items():
        result["feeders"].append({
            "cim_id": rdf_id,
            "name": feeder.name,
            "substation_cim_id": feeder.substation_id,
            "rated_mva": feeder.rated_mva,
        })

    for rdf_id, pt in cim.transformers.items():
        result["distribution_transformers"].append({
            "cim_id": rdf_id,
            "name": pt.name,
            "feeder_cim_id": pt.equipment_container_id,
            "rated_kva": pt.rated_kva,
            "voltage_hv_kv": pt.rated_voltage_hv,
            "voltage_lv_kv": pt.rated_voltage_lv,
        })

    return result
