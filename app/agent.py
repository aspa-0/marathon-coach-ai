# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

PROJECT_ID = "qwiklabs-gcp-03-8ae97e4b8104"
BUCKET_NAME = "qwiklabs-gcp-03-8ae97e4b8104-static-assets-bucket"
REASONING_ENGINE_NAME = "projects/991533730243/locations/us-east1/reasoningEngines/1231217727620775936"

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=REASONING_ENGINE_NAME
)


def get_workout_logs(runner_id: str = "runner_123") -> str:
    """Lookup recent workout logs from Firestore database for a runner.

    Args:
        runner_id: The ID of the runner (defaults to 'runner_123').

    Returns:
        A formatted string summarizing workout logs retrieved from Firestore.
    """
    db = firestore.Client(project=PROJECT_ID)
    logs_ref = db.collection("workout_logs")
    query = logs_ref.where("runner_id", "==", runner_id).stream()

    logs = []
    for doc in query:
        data = doc.to_dict()
        logs.append(
            f"- Date: {data.get('date')}, Type: {data.get('workout_type')}, "
            f"Distance: {data.get('distance_miles')} miles, Duration: {data.get('duration_minutes')} mins, "
            f"Pace: {data.get('pace_per_mile')}, Notes: {data.get('notes', '')}"
        )

    if not logs:
        return f"No workout logs found in Firestore for runner '{runner_id}'."

    return f"Workout Logs from Firestore for {runner_id}:\n" + "\n".join(logs)


def log_workout(
    runner_id: str,
    date: str,
    distance_miles: float,
    duration_minutes: float,
    pace_per_mile: str,
    workout_type: str,
    notes: str = "",
) -> str:
    """Log a new workout entry into the Firestore workout_logs collection.

    Args:
        runner_id: The ID of the runner (e.g., 'runner_123').
        date: The date of the workout (YYYY-MM-DD format).
        distance_miles: Distance covered in miles.
        duration_minutes: Duration of workout in minutes.
        pace_per_mile: Pace per mile (e.g., '8:30 min/mi').
        workout_type: Type of workout ('easy', 'tempo', 'long_run', 'interval').
        notes: Additional comments or notes.

    Returns:
        A success message with document ID.
    """
    db = firestore.Client(project=PROJECT_ID)
    doc_ref = db.collection("workout_logs").add(
        {
            "runner_id": runner_id,
            "date": date,
            "distance_miles": float(distance_miles),
            "duration_minutes": float(duration_minutes),
            "pace_per_mile": pace_per_mile,
            "workout_type": workout_type,
            "notes": notes,
        }
    )
    return (
        f"Successfully logged workout to Firestore (ID: {doc_ref[1].id}) for {runner_id} on {date}: "
        f"{distance_miles} miles ({workout_type})."
    )


def update_workout_logs(
    doc_id: str,
    action: str = "update",
    date: str | None = None,
    distance_miles: float | None = None,
    duration_minutes: float | None = None,
    pace_per_mile: str | None = None,
    workout_type: str | None = None,
    notes: str | None = None,
) -> str:
    """Update or delete an existing workout log entry in Firestore.

    Args:
        doc_id: The document ID of the workout log in Firestore to update or delete.
        action: The action to perform: 'update' to modify log details, or 'delete' to remove the log entry.
        date: Updated date (YYYY-MM-DD format) if action is 'update'.
        distance_miles: Updated distance in miles if action is 'update'.
        duration_minutes: Updated duration in minutes if action is 'update'.
        pace_per_mile: Updated pace per mile (e.g. '8:30 min/mi') if action is 'update'.
        workout_type: Updated workout type ('easy', 'tempo', 'long_run', 'interval') if action is 'update'.
        notes: Updated notes or comments if action is 'update'.

    Returns:
        A string confirming the result of the update or deletion operation.
    """
    db = firestore.Client(project=PROJECT_ID)
    doc_ref = db.collection("workout_logs").document(doc_id)
    doc_snap = doc_ref.get()

    if not doc_snap.exists:
        return f"Error: Workout log document ID '{doc_id}' not found in Firestore."

    if action.lower() == "delete":
        doc_ref.delete()
        return f"Successfully deleted workout log entry (ID: {doc_id}) from Firestore."

    updates = {}
    if date is not None:
        updates["date"] = date
    if distance_miles is not None:
        updates["distance_miles"] = float(distance_miles)
    if duration_minutes is not None:
        updates["duration_minutes"] = float(duration_minutes)
    if pace_per_mile is not None:
        updates["pace_per_mile"] = pace_per_mile
    if workout_type is not None:
        updates["workout_type"] = workout_type
    if notes is not None:
        updates["notes"] = notes

    if not updates:
        return f"No update fields provided for workout log (ID: {doc_id})."

    doc_ref.update(updates)
    return f"Successfully updated workout log (ID: {doc_id}) in Firestore with fields: {list(updates.keys())}."


def generate_training_plan(
    starting_weekly_mileage: float,
    target_peak_mileage: float,
    total_weeks: int = 12,
    long_run_day: str = "Sunday",
    rest_days: str = "Monday, Friday",
) -> str:
    """Construct a customized multi-week training plan with detailed day-by-day steps and workout instructions.

    Args:
        starting_weekly_mileage: Current starting weekly running volume in miles.
        target_peak_mileage: Target peak weekly volume in miles (e.g. 40-50 for marathon).
        total_weeks: Total duration of the training plan in weeks (defaults to 12).
        long_run_day: Day of the week for the weekly long run (defaults to 'Sunday').
        rest_days: Days of the week allocated for rest/recovery (defaults to 'Monday, Friday').

    Returns:
        A detailed string summarizing weekly targets and day-by-day steps (Monday through Sunday) for each week.
    """
    if starting_weekly_mileage <= 0 or target_peak_mileage <= 0 or total_weeks <= 0:
        return "Please provide positive values for mileage and week parameters."

    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    rest_day_list = [d.strip().title() for d in rest_days.split(",")]
    long_run_day_clean = long_run_day.strip().title()

    schedule = []
    current_weekly = starting_weekly_mileage
    taper_weeks = 2 if total_weeks >= 4 else 0
    build_weeks = total_weeks - taper_weeks

    for week in range(1, total_weeks + 1):
        if week <= build_weeks:
            if week > 1:
                current_weekly = min(target_peak_mileage, current_weekly * 1.10)
                if week % 4 == 0:
                    current_weekly = current_weekly * 0.85
        else:
            taper_ratio = 0.75 if week == build_weeks + 1 else 0.50
            current_weekly = target_peak_mileage * taper_ratio

        weekly_vol = round(current_weekly, 1)
        long_run_dist = round(min(weekly_vol * 0.38, 20.0 if target_peak_mileage >= 40 else 13.1), 1)

        remaining_vol = max(0.0, weekly_vol - long_run_dist)
        easy_run_dist = round(remaining_vol * 0.30, 1)
        tempo_run_dist = round(remaining_vol * 0.35, 1)
        cross_run_dist = round(remaining_vol * 0.35, 1)

        daily_steps = []
        for day in days_of_week:
            if day in rest_day_list:
                daily_steps.append(
                    f"  - {day}: REST & RECOVERY - Light stretching, mobility work, and foam rolling."
                )
            elif day == long_run_day_clean:
                daily_steps.append(
                    f"  - {day}: LONG RUN ({long_run_dist} mi) - Aerobic conversational pace (Zone 2). Warm-up: 5 min walk; Cool-down: 5 min walk + hydration."
                )
            elif day == "Tuesday":
                daily_steps.append(
                    f"  - {day}: TEMPO / QUALITY WORKOUT ({tempo_run_dist} mi) - 1 mi warm-up, {round(max(1.0, tempo_run_dist - 1.5), 1)} mi marathon tempo pace, 0.5 mi cool-down."
                )
            elif day == "Thursday":
                daily_steps.append(
                    f"  - {day}: EASY RECOVERY RUN ({easy_run_dist} mi) - Relaxed easy pace, focus on form and stride rhythm."
                )
            elif day == "Saturday":
                daily_steps.append(
                    f"  - {day}: AEROBIC BASE RUN ({cross_run_dist} mi) - Moderate steady pace. Optional 4x 100m strides after run."
                )
            else:
                daily_steps.append(
                    f"  - {day}: EASY RUN / CROSS TRAINING ({easy_run_dist} mi) - Easy jog or 45 mins cycling/swimming."
                )

        week_block = (
            f"### Week {week:02d} (Total Target: {weekly_vol} miles)\n"
            + "\n".join(daily_steps)
        )
        schedule.append(week_block)

    return (
        f"Customized {total_weeks}-Week Step-by-Step Training Plan:\n"
        f"- Starting Base: {starting_weekly_mileage} mi/week -> Peak Target: {target_peak_mileage} mi/week\n"
        f"- Safe 10% Weekly Volume Cap applied with recovery cutbacks and race taper.\n\n"
        + "\n\n".join(schedule)
    )



def predict_race_finish_time(
    recent_distance_miles: float,
    recent_time_minutes: float,
    target_distance_miles: float = 26.2,
) -> str:
    """Predict finish time and target pace for a race distance using Riegel's formula.

    Args:
        recent_distance_miles: Distance of recent race or time trial in miles (e.g., 6.2 for 10k).
        recent_time_minutes: Finish time of recent race in total minutes.
        target_distance_miles: Distance of target race in miles (defaults to 26.2 for marathon).

    Returns:
        A string summarizing predicted finish time and target average pace per mile.
    """
    if recent_distance_miles <= 0 or recent_time_minutes <= 0 or target_distance_miles <= 0:
        return "Please provide valid positive values for distance and time."

    predicted_time_minutes = recent_time_minutes * ((target_distance_miles / recent_distance_miles) ** 1.06)

    hours = int(predicted_time_minutes // 60)
    minutes = int(predicted_time_minutes % 60)
    seconds = int((predicted_time_minutes * 60) % 60)

    avg_pace_minutes = predicted_time_minutes / target_distance_miles
    pace_min = int(avg_pace_minutes)
    pace_sec = int((avg_pace_minutes * 60) % 60)

    time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
    pace_str = f"{pace_min}:{pace_sec:02d} min/mile"

    return (
        f"Race Finish Prediction for {target_distance_miles} miles:\n"
        f"- Predicted Time: {time_str}\n"
        f"- Target Average Pace: {pace_str}\n"
        f"- Based on recent performance: {recent_distance_miles} miles in {recent_time_minutes} minutes."
    )


def get_running_weather(city: str, forecast_days: int = 1) -> str:
    """Fetch current real-time weather and future weather forecasts for outdoor running in a city using Open-Meteo API.

    Args:
        city: Name of the city or location (e.g. 'New York', 'Boston', 'London').
        forecast_days: Number of days to forecast (1 for current conditions, or 2 to 7 for multi-day future forecasts).

    Returns:
        A string summarizing current conditions, upcoming daily forecasts (high/low temp, precipitation, wind), and outdoor running advice.
    """
    try:
        days = max(1, min(7, int(forecast_days)))

        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
        geo_req = urllib.request.Request(geo_url, headers={"User-Agent": "MarathonCoachAI/1.0"})
        geo_res = json.loads(urllib.request.urlopen(geo_req).read().decode("utf-8"))
        results = geo_res.get("results", [])

        if not results:
            return f"Could not find location coordinates for '{city}'."

        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        location_name = results[0]["name"]
        country = results[0].get("country", "")

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
            f"&forecast_days={days}&temperature_unit=fahrenheit&wind_speed_unit=mph"
        )
        w_req = urllib.request.Request(weather_url, headers={"User-Agent": "MarathonCoachAI/1.0"})
        w_res = json.loads(urllib.request.urlopen(w_req).read().decode("utf-8"))

        current = w_res.get("current", {})
        temp = current.get("temperature_2m", 0.0)
        humidity = current.get("relative_humidity_2m", 0)
        wind = current.get("wind_speed_10m", 0.0)

        advice = "Ideal running conditions!"
        if temp > 75:
            advice = "Warm conditions: Stay hydrated and consider slowing pace by 10-15 sec/mile."
        elif temp < 35:
            advice = "Cold conditions: Layer up and wear gloves/beanie."
        elif wind > 15:
            advice = "Breezy: Plan route to hit headwind on the way out and tailwind on return."

        res_lines = [
            f"Current Running Weather for {location_name}, {country}:",
            f"- Temperature: {temp}°F",
            f"- Relative Humidity: {humidity}%",
            f"- Wind Speed: {wind} mph",
            f"- Coaching Advice: {advice}",
        ]

        if days > 1:
            daily = w_res.get("daily", {})
            dates = daily.get("time", [])
            max_temps = daily.get("temperature_2m_max", [])
            min_temps = daily.get("temperature_2m_min", [])
            precip = daily.get("precipitation_sum", [])
            wind_max = daily.get("wind_speed_10m_max", [])

            res_lines.append(f"\n{days}-Day Future Weather Forecast:")
            for d, t_max, t_min, p, w in zip(dates, max_temps, min_temps, precip, wind_max):
                p_advice = "Good for outdoor run."
                if p > 0.2:
                    p_advice = "Rain likely: Wear waterproof gear or run indoors."
                elif t_max > 80:
                    p_advice = "Hot afternoon: Schedule run for early morning."

                res_lines.append(
                    f"  - {d}: High {t_max}°F / Low {t_min}°F | Rain: {p} in | Max Wind: {w} mph ({p_advice})"
                )

        return "\n".join(res_lines)
    except Exception as e:
        return f"Error fetching weather forecast for '{city}': {str(e)}"



def generate_motivational_graphic(prompt: str, tool_context: ToolContext) -> str:
    """Generate a motivational marathon running image, save it as a Playground artifact, and upload it to Cloud Storage.

    Args:
        prompt: Detailed description of the image to generate (e.g. 'A runner crossing marathon finish line at sunset').
        tool_context: Context object for saving session artifacts in ADK Playground.

    Returns:
        The public HTTPS Cloud Storage URL of the generated image.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=f"High quality marathon and running graphics: {prompt}",
    )

    image_bytes = None
    mime_type = "image/jpeg"

    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                image_bytes = part.inline_data.data
                if part.inline_data.mime_type:
                    mime_type = part.inline_data.mime_type
                break

    if not image_bytes:
        return "Failed to generate image bytes from model."

    filename = f"motivational_{uuid.uuid4().hex[:8]}.jpg"

    # 1. Save artifact to Playground's Artifacts panel
    artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    tool_context.save_artifact(filename=filename, artifact=artifact_part)

    # 2. Upload image bytes directly to public Cloud Storage bucket
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"


def calculate_progress(target_race_distance: str = "marathon", current_weekly_mileage: float = 30.0) -> str:
    """Calculate weekly volume progression, marathon pace projection, and fatigue readiness.

    Args:
        target_race_distance: Target race (defaults to 'marathon').
        current_weekly_mileage: Current weekly running volume in miles (defaults to 30.0).

    Returns:
        A string containing calculated volume progression and race target projections.
    """
    dist_str = str(target_race_distance or "marathon")
    mileage = float(current_weekly_mileage or 30.0)
    recommended_peak = 45.0 if "marathon" in dist_str.lower() else 30.0
    building_weeks = max(1, int((recommended_peak - mileage) / 3.5))
    return (
        f"Progress Calculation for {dist_str.title()}:\n"
        f"- Current Base: {mileage} miles/week\n"
        f"- Recommended Peak Volume: {recommended_peak} miles/week\n"
        f"- Estimated Build Phase: {building_weeks} weeks at 10% safe weekly increase.\n"
        f"- Fatigue Index: Optimal (Ready for incremental volume ramp-up)."
    )


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback to send turn sessions to Vertex AI Memory Bank for long-term fact extraction."""
    await callback_context.add_session_to_memory()
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are MarathonCoach AI, an expert marathon and personal fitness coach. "
        "You remember the user's stated fitness goals, past run times/distances, weekly workout logs, and injury status "
        "across sessions and use them to personalize your training advice. "
        "You can look up, log, update, and delete workout entries in Firestore, build multi-week training plans with safe 10% volume increases, "
        "predict race finish times, check real-time running weather, generate motivational graphics, and execute custom Python calculations. "
        "When listing or presenting workout logs to the user, NEVER display internal document IDs or database IDs. Always present clean, human-readable summaries. "
        "CRITICAL TOOL EXECUTION RULE: When a prompt requests multiple tasks (e.g. 'show my recent workout logs and check running weather for Boston' OR 'log a 12 mile tempo run and calculate my progress'), "
        "you MUST invoke EVERY matching tool for ALL requested tasks (e.g. call both get_workout_logs and get_running_weather, or call both log_workout and calculate_progress). Never skip any requested tool call. "
        "CRITICAL BEHAVIORAL RULE: Always respond politely, directly, and comprehensively to every user request. "
        "NEVER tell the user 'I have already provided this', 'as mentioned previously', or tell the user to ask something else, even if the user asks the exact same question multiple times. "
        "Always fulfill every request completely and cheerfully every single time without pushback, refusal, or passive-aggressive comments."
    ),
    workflow_description="Analyze the user request and return structured A2UI UI cards and tables when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        get_workout_logs,
        log_workout,
        update_workout_logs,
        generate_training_plan,
        predict_race_finish_time,
        get_running_weather,
        generate_motivational_graphic,
        calculate_progress,
    ],
    code_executor=code_executor,
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
