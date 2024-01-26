import { useState } from "react";
import { Typography } from "@material-tailwind/react";
import { XCircleIcon } from "@heroicons/react/24/outline";

import { deleteEvent } from "../api/agentd";

function EditableEvent({ data, onDelete }) {
  const renderEventData = () => {
    switch (data.type) {
      case "scroll":
        return <Property k="Scroll Data" v={data.scroll_data} />;
      case "click":
        return <Property k="Click Data" v={data.click_data} />;
      case "key":
        return <Property k="Key Data" v={data.key_data} />;
      case "text":
        return <Property k="Text Data" v={data.text_data} />;
      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col border border-black mt-4 p-2 rounded-2xl relative">
      {/* Delete button with X icon */}
      <button onClick={onDelete} className="absolute top-0 right-0 p-1">
        <XCircleIcon className="h-5 w-5" />
      </button>

      <Property k="Type" v={data.type} />
      <Property
        k="Timestamp"
        v={new Date(data.timestamp * 1000).toISOString()}
      />
      <Property
        k="Coordinates"
        v={`(${data.coordinates.x}, ${data.coordinates.y})`}
      />
      {renderEventData()}
    </div>
  );
}

function Event({ data }) {
  // id: str
  // type: str
  // timestamp: float
  // coordinates: CoordinatesModel
  // screenshot_path: Optional[str]
  // screenshot_b64: Optional[str]
  // click_data: Optional[ClickData]
  // key_data: Optional[KeyData]
  // scroll_data = Optional[ScrollData]
  const renderEventData = () => {
    switch (data.type) {
      case "scroll":
        return <Property k="Scroll Data" v={data.scroll_data} />;
      case "click":
        return <Property k="Click Data" v={data.click_data} />;
      case "key":
        return <Property k="Key Data" v={data.key_data} />;
      case "text":
        return <Property k="Text Data" v={data.text_data} />;
      default:
        return null;
    }
  };
  return (
    <div className="flex flex-col border border-black mt-4 p-2 rounded-2xl">
      <Property k="Type" v={data.type} />
      <Property
        k="Timestamp"
        v={new Date(data.timestamp * 1000).toISOString()}
      />
      <Property
        k="Coordinates"
        v={`(${data.coordinates.x}, ${data.coordinates.y})`}
      />
      {renderEventData()}
      {/* <img src={`data:image/jpeg;base64,${data.screenshot_b64}`} alt="description" />; */}
    </div>
  );
}

function Property({ k, v }) {
  const renderValue = (value) => {
    if (value !== null && typeof value === "object") {
      return <pre>{JSON.stringify(value, null, 2)}</pre>;
    }
    return <p>{value}</p>;
  };

  return (
    <div className="flex flex-row gap-2">
      <p className="font-semibold">{k}:</p>
      {renderValue(v)}
    </div>
  );
}

export default function Recording({ data }) {
  // id: str
  // description: str
  // start_time: float
  // end_time: float
  // events: List[RecordedEvent] = []

  return (
    <div className="flex flex-col border border-black shadow-2xl rounded-2xl p-4 mt-6">
      <Typography variant="h5">Current Task</Typography>
      <br />
      <Property k="Description" v={data.description} />
      <Property
        k="Start time"
        v={new Date(data.start_time * 1000).toISOString()}
      />
      <p className="font-semibold">Events {"\u25BC"}</p>
      <div className="events h-96 overflow-scroll">
        {data.events.map((event, index) => (
          <Event key={index} data={event} />
        ))}
      </div>
    </div>
  );
}

export function EditableRecording({ data, agentdAddr }) {
  const [events, setEvents] = useState(data.events);

  const handleDeleteEvent = async (index) => {
    const eventToDelete = events[index];
    try {
      const response = await deleteEvent(agentdAddr, data.id, eventToDelete.id);
      if (response && response.status === "success") {
        const updatedEvents = events.filter(
          (_, eventIndex) => eventIndex !== index
        );
        setEvents(updatedEvents);
      } else {
        console.error("Failed to delete the event");
      }
    } catch (error) {
      console.error("Error deleting event:", error);
    }
  };

  return (
    <div className="flex flex-col border border-black shadow-2xl rounded-2xl p-4 mt-6">
      <p className="font-semibold">Events {"\u25BC"}</p>
      <div className="events h-96 overflow-scroll">
        {events.map((event, index) => (
          <EditableEvent
            key={event.id}
            data={event}
            onDelete={() => handleDeleteEvent(index)}
          />
        ))}
      </div>
    </div>
  );
}
