#!/usr/bin/env python3
import traceback
from typing import Dict, List, Tuple
import pathlib
from enum import Enum
from contextlib import suppress
from io import StringIO
import math
import pickle
import argparse
import warnings
import re
from datetime import datetime
import pytz

import tzlocal
import streamlit as st

import boto3
from boto3.dynamodb.conditions import Key
from botocore.client import Config
from botocore.exceptions import ClientError

import pandas
import numpy as np

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.ticker import FuncFormatter
import matplotlib.pyplot as plt


"""
Utility to view the Box Build history of a TR or OC and generate a report of power tests that were run.

Units can be looked up by Serial Number or MAC Address.

Reports can be batched by using the --loadfile <filename> option to load a text file containing Serial Numbers or
Mac Addresses, one per line.
"""

warnings.filterwarnings("ignore")


class DatabaseName(Enum):
    PRODUCTION = "HardwareProduction"
    DEVELOPMENT = "DevHardwareProduction"


class InputType(Enum):
    SERIALNUMBER = "SN: "
    MACADDRESS = "Mac: "
    UNKNOWN = "Unknown"


class ReportType(Enum):
    SELECT_FROM_DATA = 1
    OC_REPORT = 2


def detect_serial_or_mac(user_input: str) -> Tuple[str, InputType]:
    if re.match(r"^[0-9A-F]{10}$", user_input):
        return user_input, InputType.SERIALNUMBER
    elif re.match(r"^(WX[0-9A-F]{8}|A[0-9A-F]{9}|B[0-9A-F]{9})$", user_input):
        return user_input, InputType.SERIALNUMBER
    elif re.match(r"^34D954[0-9A-F]{6}$", user_input):
        return user_input, InputType.MACADDRESS
    elif re.match(r"^34[:.\-]D9[:.\-]54[:.\-][0-9A-F]{2}[:.\-][0-9A-F]{2}[:.\-][0-9A-F]{2}$", user_input):
        mac_address = re.sub(r"[:.\-]", "", user_input)
        return mac_address, InputType.MACADDRESS
    else:
        return None, InputType.UNKNOWN


def create_pdf_from_record(data: List[Dict], passed: bool, plot_pdf: PdfPages, selection) -> bool:
    def plot(
        axes: plt.Axes,
        device: str,
        value_title: str,
        label: str,
        linestyle: str,
        color: str,
    ):
        device_data = charge_test_data[device]
        with suppress(KeyError):
            axes.plot(
                device_data["Timestamp"],
                device_data[value_title],
                label=f"{device.upper()}: {label}",
                linestyle=linestyle,
                color=color,
            )

    model_number = data[selection]["config"]["ids"]["mn"]
    system_type = model_number[:2]
    if system_type not in ("TR", "OC"):
        print("This serial number is not for a TR or OC")
        return False

    datalog_names = [key for key in sorted(data[selection].keys()) if "datalog_" in key]
    if not datalog_names:
        print("The selected record does not contain any saved data values, unable to create the report")
        return False

    result = "Passed" if passed else "Failed"

    if "TR" in system_type and data[selection]["type"] == ReportType.SELECT_FROM_DATA:
        fig, main_axs = plt.subplots()
        fig.set_figwidth(12)
        fig.set_figheight(8)
        sec_axs = main_axs.twinx()

        charge_test_data = {}
        charge_test_data["tr"] = pandas.read_csv(StringIO(data[selection]["datalog_wireless_charge"]["tr"]))
        charge_test_data["tr"]["Timestamp"] -= charge_test_data["tr"]["Timestamp"].iloc[0]

        if "Messages" in charge_test_data["tr"]:
            messages = charge_test_data["tr"][charge_test_data["tr"]["Messages"].notnull()]
            collated_messages = [
                f"Time: {timestamp:03d} - {value}"
                for timestamp, value in zip(messages["Timestamp"], messages["Messages"])
            ]
        else:
            collated_messages = []

        charge_test_data["tr"]["CalcWPa"] = (
            charge_test_data["tr"]["VMonPa"] * charge_test_data["tr"]["IMonPa"]
        )

        plot(main_axs, "tr", "CalcWPa", "TX Power [W]", "solid", "r")
        plot(main_axs, "tr", "VMonPa", "VMonPa [V]", "solid", "k")
        plot(sec_axs, "tr", "TMonPa", "TMonPa [°C]", "dotted", "r")
        plot(sec_axs, "tr", "TMonAmb", "TMonAmb [°C]", "dotted", "#fdb147")

        sec_axs.axhline(
            data[selection].get("charge_test_ambient_temp", 0),
            label="Amb. Temp [°C]",
            color="g",
            linestyle="dotted",
        )

        main_axs.legend(loc="upper left")
        sec_axs.legend(loc="lower right", ncol=3)
        main_axs.set_title(
            f"Test: Wireless Charge | Model: {data[selection]['config']['ids']['mn']} | "
            f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
        )
        main_axs.set_xlabel("Time [s]")
        main_axs.set_ylabel("Volts / Power")
        sec_axs.set_ylabel("Temp [C]")
        main_axs.grid(True)
        main_axs.set_ylim((0, int(math.ceil(main_axs.get_ybound()[1] / 50) * 50)))
        sec_axs.set_ylim((0, int(math.ceil(sec_axs.get_ybound()[1] / 67.5) * 67.5)))
        main_axs.set_yticks(np.linspace(0, main_axs.get_ybound()[1], 10))
        sec_axs.set_yticks(np.linspace(0, sec_axs.get_ybound()[1], 10))
        main_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:>4.0f}"))
        sec_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:<4.0f}"))

        if collated_messages:
            plt.figtext(
                x=0.05,
                y=0.05,
                s="\n".join(collated_messages),
                multialignment="left",
                verticalalignment="top",
            )

        plt.text(2, 1, f"Mac: {data[selection]['mac']}")
        plt.savefig(plot_pdf, format="pdf", bbox_inches="tight")
        plt.close()

        try:
            table_data = {
                "Test": [
                    "ready_led",
                    "charging_led",
                    "fault_led",
                    "Fans",
                    "PA Temperature",
                    "DC-DC Temperature",
                ],
                "Low Limit": [
                    "NA",
                    "NA",
                    "NA",
                    "NA",
                    f"{data[selection]['tolerance_checks']['Wireless Charging PA Temp [TMonPa]']['lower_limit']}",
                    f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['lower_limit']}",
                ],
                "High Limit": [
                    "NA",
                    "NA",
                    "NA",
                    "NA",
                    f"{data[selection]['tolerance_checks']['Wireless Charging PA Temp [TMonPa]']['upper_limit']}",
                    f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['upper_limit']}",
                ],
                "Actual": [
                    "NA",
                    "NA",
                    "NA",
                    "NA",
                    f"{data[selection]['tolerance_checks']['Wireless Charging PA Temp [TMonPa]']['actual']}",
                    f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['actual']}",
                ],
                "Result": [
                    "Pass" if data[selection]["pass_fail_prompts"]["ready_led"] else "Fail",
                    "Pass" if data[selection]["pass_fail_prompts"]["charging_led"] else "Fail",
                    "Pass" if data[selection]["pass_fail_prompts"]["fault_led"] else "Fail",
                    "Pass" if data[selection]["pass_fail_prompts"]["fan_1"] else "Fail",
                    "Pass" if data[selection]["tolerance_checks"]["Wireless Charging PA Temp [TMonPa]"]["pass"] else "Fail",
                    "Pass" if data[selection]["tolerance_checks"]["Wireless Charging DC-DC Temp [TMonAmb]"]["pass"] else "Fail",
                ],
            }
            df = pandas.DataFrame(table_data)
            fig, ax = plt.subplots()
            fig.set_figwidth(12)
            fig.set_figheight(8)
            ax.axis("tight")
            ax.axis("off")
            table = ax.table(cellText=df.values, colLabels=df.columns, cellLoc="center", loc="center")
            table.auto_set_font_size(False)
            table.set_fontsize(11)
            for (_, _), cell in table.get_celld().items():
                cell.set_height(0.09)
            ax.set_title(
                f"Test: Tolerance Checks | Model: {data[selection]['config']['ids']['mn']} | "
                f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
            )
            plt.savefig(plot_pdf, format="pdf")
            plt.close()
        except Exception as e:
            print(f"Tolerance check data is not available: {e=}")

    else:
        if "datalog_wireless_charge" in datalog_names:
            charge_test_data = {}
            charge_test_data["oc"] = pandas.read_csv(StringIO(data[selection]["datalog_wireless_charge"]["oc"]))
            charge_test_data["bat"] = pandas.read_csv(StringIO(data[selection]["datalog_wireless_charge"]["bat"]))

            charge_test_data["oc"]["Timestamp"] -= charge_test_data["oc"]["Timestamp"].iloc[0]
            charge_test_data["bat"]["Timestamp"] -= charge_test_data["bat"]["Timestamp"].iloc[0]

            charge_test_data["oc"]["CalcWBatt"] = charge_test_data["oc"]["VMonBatt"] * charge_test_data["oc"]["IBattery"]
            charge_test_data["oc"]["Current"] = -charge_test_data["bat"]["Current"]
            charge_test_data["oc"]["Power"] = -charge_test_data["bat"]["Power"]
            charge_test_data["bat"]["Power"] = -charge_test_data["bat"]["Current"] * charge_test_data["bat"]["Voltage"]

            fig, wireless_main_axs = plt.subplots()
            fig.set_figwidth(12)
            fig.set_figheight(8)
            wireless_sec_axs = wireless_main_axs.twinx()

            plot(wireless_main_axs, "oc", "CalcWBatt", "Batt Power [W]", "solid", "b")
            plot(wireless_main_axs, "bat", "Power", "Sim Power [W]", "solid", "g")
            plot(wireless_sec_axs, "oc", "TBoard", "TBoard [°C]", "dotted", "b")
            plot(wireless_sec_axs, "oc", "TCharger", "TCharger [°C]", "dotted", "m")
            plot(wireless_sec_axs, "oc", "VRect", "VRect [V]", "dashed", "k")

            if data[selection]["type"] == ReportType.SELECT_FROM_DATA:
                title = (
                    f"Test: Wireless Charge | Model: {data[selection]['config']['ids']['mn']} | "
                    f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
                )
            else:
                title = (
                    f"Test: Wireless Charge | Mac: {data[selection]['oc_mac']} | "
                    f"Date/Time: {data[selection]['time']} | Result: {result}"
                )

            wireless_main_axs.legend(loc="upper left")
            wireless_sec_axs.legend(loc="lower right", ncol=3)
            wireless_main_axs.set_title(title)
            wireless_main_axs.set_xlabel("Time [s]")
            wireless_main_axs.set_ylabel("Power")
            wireless_sec_axs.set_ylabel("Volts / Temp [C]")
            wireless_main_axs.grid(True)
            wireless_main_axs.set_ylim((0, int(math.ceil(wireless_main_axs.get_ybound()[1] / 50) * 50)))
            wireless_sec_axs.set_ylim((0, int(math.ceil(wireless_sec_axs.get_ybound()[1] / 67.5) * 67.5)))
            wireless_main_axs.set_yticks(np.linspace(0, wireless_main_axs.get_ybound()[1], 10))
            wireless_sec_axs.set_yticks(np.linspace(0, wireless_sec_axs.get_ybound()[1], 10))
            wireless_main_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:>4.0f}"))
            wireless_sec_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:<4.0f}"))

            plt.savefig(plot_pdf, format="pdf", bbox_inches="tight")
            plt.close(fig)

        if "datalog_wall_power_charge" in datalog_names:
            charge_test_data = {}
            charge_test_data["oc"] = pandas.read_csv(StringIO(data[selection]["datalog_wall_power_charge"]["oc"]))
            charge_test_data["bat"] = pandas.read_csv(StringIO(data[selection]["datalog_wall_power_charge"]["bat"]))

            charge_test_data["oc"]["Timestamp"] -= charge_test_data["oc"]["Timestamp"].iloc[0]
            charge_test_data["bat"]["Timestamp"] -= charge_test_data["bat"]["Timestamp"].iloc[0]

            charge_test_data["oc"]["CalcWBatt"] = charge_test_data["oc"]["VMonBatt"] * charge_test_data["oc"]["IBattery"]
            charge_test_data["oc"]["Current"] = -charge_test_data["bat"]["Current"]
            charge_test_data["oc"]["Power"] = -charge_test_data["bat"]["Power"]

            fig, wall_main_axs = plt.subplots()
            fig.set_figwidth(12)
            fig.set_figheight(8)
            wall_sec_axs = wall_main_axs.twinx()

            plot(wall_sec_axs, "oc", "IBattery", "IBattery [A]", "dotted", "r")
            plot(wall_sec_axs, "bat", "Current", "Current [A]", "dashed", "y")
            plot(wall_main_axs, "oc", "CalcWBatt", "Batt Power [W]", "solid", "b")
            plot(wall_main_axs, "oc", "Power", "Sim Power [W]", "solid", "g")
            plot(wall_sec_axs, "oc", "TBoard", "TBoard [°C]", "dotted", "b")
            plot(wall_sec_axs, "oc", "TCharger", "TCharger [°C]", "dotted", "m")
            plot(wall_sec_axs, "oc", "VRect", "VRect [V]", "dashed", "k")

            wall_main_axs.legend(loc="upper left")
            wall_sec_axs.legend(loc="lower right", ncol=3)
            wall_main_axs.set_title(
                f"Test: Wall Power | Model: {data[selection]['config']['ids']['mn']} | "
                f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
            )
            wall_main_axs.set_xlabel("Time [s]")
            wall_main_axs.set_ylabel("Power")
            wall_sec_axs.set_ylabel("Amps / Volts / Temp[C]")
            wall_main_axs.grid(True)
            wall_main_axs.set_ylim((0, int(math.ceil(wall_main_axs.get_ybound()[1] / 50) * 50)))
            wall_sec_axs.set_ylim((0, int(math.ceil(wall_sec_axs.get_ybound()[1] / 67.5) * 67.5)))
            wall_main_axs.set_yticks(np.linspace(0, wall_main_axs.get_ybound()[1], 10))
            wall_sec_axs.set_yticks(np.linspace(0, wall_sec_axs.get_ybound()[1], 10))
            wall_main_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:>4.0f}"))
            wall_sec_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:<4.0f}"))

            plt.savefig(plot_pdf, format="pdf", bbox_inches="tight")
            plt.close(fig)

        if "datalog_float_voltage_test" in datalog_names:
            charge_test_data = {}
            charge_test_data["oc"] = pandas.read_csv(StringIO(data[selection]["datalog_float_voltage_test"]["oc"]))
            charge_test_data["bat"] = pandas.read_csv(StringIO(data[selection]["datalog_float_voltage_test"]["bat"]))

            charge_test_data["oc"]["Timestamp"] -= charge_test_data["oc"]["Timestamp"].iloc[0]
            charge_test_data["bat"]["Timestamp"] -= charge_test_data["bat"]["Timestamp"].iloc[0]

            fig, float_main_axs = plt.subplots()
            fig.set_figwidth(12)
            fig.set_figheight(8)

            plot(float_main_axs, "oc", "VMonBatt", "VMonBat [V]", "solid", "b")
            plot(float_main_axs, "bat", "Voltage", "Voltage [V]", "solid", "g")

            float_main_axs.legend(loc="upper left")
            float_main_axs.set_title(
                f"Test: Float Voltage | Model: {data[selection]['config']['ids']['mn']} | "
                f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
            )
            float_main_axs.set_xlabel("Time [s]")
            float_main_axs.set_ylabel("Voltage")
            float_main_axs.grid(True)
            float_main_axs.set_yticks(np.linspace(0, float_main_axs.get_ybound()[1], 10))
            float_main_axs.get_yaxis().set_major_formatter(FuncFormatter(lambda x, p: f"{x:>4.0f}"))

            plt.savefig(plot_pdf, format="pdf", bbox_inches="tight")
            plt.close(fig)

        try:
            if data[selection]["type"] == ReportType.SELECT_FROM_DATA:
                table_data = {
                    "Test": [
                        "Median\nVolt vs. Sim",
                        "Median\nFloat vs. Sim",
                        "Median\nFloat vs. Setpoint",
                        "Charge Current",
                        "Median\nCurrent vs. Sim",
                        "Median Current\nvs. OC Max Setting",
                        "TCharger",
                        "TBoard",
                        "TDC-DC",
                    ],
                    "Low Limit": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Charger Voltage']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Setpoint']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs Bat Sim']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs OC Max Setting']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['lower_limit']}",
                    ],
                    "High Limit": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Charger Voltage']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Setpoint']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs Bat Sim']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs OC Max Setting']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['upper_limit']}",
                    ],
                    "Actual": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['actual']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Charger Voltage']['actual']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Float Voltage vs Setpoint']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['actual']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs Bat Sim']['actual']}",
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Current vs OC Max Setting']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging DC-DC Temp [TMonAmb]']['actual']}",
                    ],
                    "Result": [
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Charge Voltage vs Bat Sim"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Float Voltage vs Charger Voltage"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Float Voltage vs Setpoint"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging Current [IBattery]"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Charge Current vs Bat Sim"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Charge Current vs OC Max Setting"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging OC Charger Temp [TCharger]"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging OC Board Temp [TBoard]"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging DC-DC Temp [TMonAmb]"]["pass"] else "Fail",
                    ],
                }
                title = (
                    f"Test: Tolerance Checks | Model: {data[selection]['config']['ids']['mn']} | "
                    f"ID: {data[selection]['serial']} | Date/Time: {data[selection]['time']} | Result: {result}"
                )
            else:
                table_data = {
                    "Test": ["Median\nVolt vs. Sim", "Charge Current", "TCharger", "TBoard"],
                    "Low Limit": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['lower_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['lower_limit']}",
                    ],
                    "High Limit": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['upper_limit']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['upper_limit']}",
                    ],
                    "Actual": [
                        f"{data[selection]['tolerance_checks']['Median: OC Charge Voltage vs Bat Sim']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging Current [IBattery]']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Charger Temp [TCharger]']['actual']}",
                        f"{data[selection]['tolerance_checks']['Wireless Charging OC Board Temp [TBoard]']['actual']}",
                    ],
                    "Result": [
                        "Pass" if data[selection]["tolerance_checks"]["Median: OC Charge Voltage vs Bat Sim"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging Current [IBattery]"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging OC Charger Temp [TCharger]"]["pass"] else "Fail",
                        "Pass" if data[selection]["tolerance_checks"]["Wireless Charging OC Board Temp [TBoard]"]["pass"] else "Fail",
                    ],
                }
                title = (
                    f"Test: Tolerance Checks | Mac: {data[selection]['oc_mac']} | "
                    f"Date/Time: {data[selection]['time']} | Result: {result}"
                )

            df = pandas.DataFrame(table_data)
            fig, ax = plt.subplots()
            fig.set_figwidth(12)
            fig.set_figheight(8)
            ax.axis("tight")
            ax.axis("off")
            table = ax.table(cellText=df.values, colLabels=df.columns, cellLoc="center", loc="center")
            table.auto_set_font_size(False)
            table.set_fontsize(12)
            for (_, _), cell in table.get_celld().items():
                cell.set_height(0.1)
            ax.set_title(title)
            plt.savefig(plot_pdf, format="pdf")
            plt.close(fig)
        except Exception as e:
            print(f"Tolerance Check data is not available, this page will be skipped: {e=}")

    return True


def create_report(data: List[Dict], sn_or_mac: str, selection, parent_path):
    try:
        model_number = data[selection]["config"]["ids"]["mn"]
    except KeyError:
        print("The selected record does not contain any saved data values, unable to create the report")
        return

    system_type = model_number[:2]
    if system_type not in ("TR", "OC"):
        print("This serial number is not for a TR or OC")
        return

    datalog_names = [key for key in sorted(data[selection].keys()) if "datalog_" in key]
    if not datalog_names:
        print("The selected record does not contain any saved data values, unable to create the report")
        return

    print("Starting Report Generation...")
    passed = data[selection]["passed"]
    file_name = f"{sn_or_mac}_data{'' if passed else '_failed'}.pdf"
    with PdfPages(parent_path / file_name) as pp:
        status = create_pdf_from_record(data, passed, pp, selection)
        if status:
            print(f"Report saved to: {parent_path / file_name}")
        else:
            print("No report was generated")


def get_db_table(table_name: DatabaseName):
    table_str = table_name.value

    aws_access_key = st.secrets["ACCESS_KEY"]
    aws_secret_key = st.secrets["SECRET_ACCESS_KEY"]
    aws_region = st.secrets.get("AWS_REGION", "us-west-2")

    dynamodb = boto3.Session(
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
    ).resource(
        "dynamodb",
        region_name=aws_region,
        config=Config(connect_timeout=4, retries={"mode": "standard"}),
    )

    table = dynamodb.Table(table_str)
    return table


def get_item_list_from_serial_or_mac(db: DatabaseName, table, serial_or_mac: str, input_type: InputType) -> list:
    found_items = []
    prompt_for_sure_flag = False

    if input_type == InputType.SERIALNUMBER:
        index = "serial-index"
        key_condition = "serial"
        report_type = ReportType.SELECT_FROM_DATA
        print("Query the serial-index")
    elif input_type == InputType.MACADDRESS:
        index = "mac-index"
        key_condition = "mac"
        report_type = ReportType.SELECT_FROM_DATA
        print("Query the mac-index")
    else:
        print("Unknown InputType")
        return None

    while True:
        try:
            resp = table.query(
                TableName=db.value,
                IndexName=index,
                KeyConditionExpression=Key(key_condition).eq(serial_or_mac),
                Limit=100,
            )
            tmp_items = resp["Items"]
            print_string = f"Found: {len(tmp_items)} matches "
            print("\r", end="", flush=True)
            print(print_string, end="", flush=True)

            for entry in tmp_items:
                entry["type"] = report_type

            if len(tmp_items) > 0:
                found_items.extend(tmp_items)

        except ClientError as e:
            print(f"Error communicating with the database: {e}")
            return None

        while "LastEvaluatedKey" in resp:
            resp = table.query(
                TableName=db.value,
                IndexName=index,
                KeyConditionExpression=Key(key_condition).eq(serial_or_mac),
                Limit=100,
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            tmp_items = resp["Items"]
            print_string = f"Found: {len(tmp_items)} additional items, {len(found_items)} total items "
            print("\r", end="", flush=True)
            print(print_string, end="", flush=True)

            for entry in tmp_items:
                entry["type"] = report_type

            if len(tmp_items) > 0:
                found_items.extend(tmp_items)

            if len(found_items) > 30 and not prompt_for_sure_flag:
                prompt_for_sure_flag = True
                print("")
                print("--------------------------------------------------")
                print("This unit is present in a large number of records.")
                print("It may be because this is a test or golden unit.")
                return found_items

        if index == "mac-index":
            index = "oc_mac-index"
            key_condition = "oc_mac"
            report_type = ReportType.OC_REPORT
            print("")
            print("Query the oc-mac-index")
        else:
            print("...complete")
            break

    return found_items


def create_data_report(args, item_list):
    list_count = 0
    db_open = False
    parent_path = pathlib.Path("data_reports/")
    parent_path.mkdir(parents=True, exist_ok=True)

    while True:
        if args.loadfile is not None:
            if list_count < len(item_list):
                list_entry = item_list[list_count]
                print(f"File entry is: {list_entry}")
                sn_or_mac, input_type = detect_serial_or_mac(list_entry)
                list_count += 1
            else:
                print("End of file reached, exiting")
                return
        else:
            try:
                user_input = input("Enter a serial number or mac address: ").strip().upper()
                sn_or_mac, input_type = detect_serial_or_mac(user_input)
            except KeyboardInterrupt:
                print("Keyboard interrupt, exiting...")
                return

        if input_type == InputType.UNKNOWN:
            print("The entered value is not a valid serial number or mac address. Try again.")
            continue

        if args.loadpickle:
            with open(f"{parent_path}/{sn_or_mac}_data.pickle", "rb") as file:
                data_object = pickle.load(file)
        else:
            if not db_open:
                db_selection = DatabaseName.PRODUCTION if not args.development else DatabaseName.DEVELOPMENT
                dynamodb_table = get_db_table(db_selection)
                data_object = get_item_list_from_serial_or_mac(db_selection, dynamodb_table, sn_or_mac, input_type)
                db_open = True

        summary_list = []
        if data_object is not None:
            count = len(data_object)
            for i in range(count):
                dt_utc = datetime.fromisoformat(data_object[i]["create_time"])
                dt_utc.replace(tzinfo=pytz.UTC)
                local_tz = tzlocal.get_localzone()
                dt_local = dt_utc.astimezone(local_tz)
                summary_list.append(
                    [
                        dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                        data_object[i]["config"]["procedure_name"],
                        "Passed" if data_object[i]["passed"] else "Failed",
                        data_object[i]["serial"],
                    ]
                )

            sorted_summary_list = sorted(summary_list, key=lambda x: x[0])
        else:
            count = 0

        if count > 1:
            i = 1
            for list_item in sorted_summary_list:
                print(f"{i}: {list_item}")
                i += 1

        if count == 0:
            print("No records found with this serial number")
            return False
        elif count == 1:
            print(f"1: {sorted_summary_list[0]}")
            selection = 0
        else:
            selection = 0
            for i in range(count):
                if summary_list[i][0] == sorted_summary_list[selection][0]:
                    selection = i
                    break

        if args.savepickle:
            with open(parent_path / pathlib.Path(f"{sn_or_mac}_data.pickle"), "wb") as file:
                pickle.dump(data_object, file, protocol=pickle.HIGHEST_PROTOCOL)
                print(f"Saved database object to: {parent_path / pathlib.Path(f'{sn_or_mac}_data.pickle')}")
                return

        create_report(data_object, sn_or_mac, selection, parent_path)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="WiBotic Box Build Report Generator")
        parser.add_argument(
            "--savepickle",
            action="store_true",
            default=False,
            help="Pull serial number data from the database and pickle it to disk.",
        )
        parser.add_argument(
            "--loadpickle",
            action="store_true",
            default=False,
            help="Load data from a saved pickle rather than the database.",
        )
        parser.add_argument(
            "--development",
            action="store_true",
            default=False,
            help="Use the development database instead of production.",
        )
        parser.add_argument(
            "--loadfile",
            type=str,
            default=None,
            help="Text file containing serial numbers or MAC addresses, one per line.",
        )
        args = parser.parse_args()

        if args.loadfile:
            with open(args.loadfile, mode="r", newline="") as file:
                snlist = [line.strip() for line in file]
        else:
            snlist = None

        create_data_report(args, snlist)

    except KeyboardInterrupt:
        print("")
        print("Exiting WiBotic Box Build Report Generator")
        raise SystemExit(0)
    except SystemExit:
        print("")
        print("Exiting WiBotic Box Build Report Generator on SystemExit")
        raise
    except Exception as e:
        print("")
        traceback.print_exception(e)
        raise SystemExit(1)
