export async function getHealth(addr) {
  const url = new URL("/health", addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to fetch health", error);
  }
}

export async function getMouseCoordinates(addr) {
  const url = new URL("/mouse_coordinates", addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to fetch mouse coordinates", error);
  }
}

export async function openUrl(addr, urlToOpen) {
  const url = new URL("/open_url", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ url: urlToOpen }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to open URL", error);
  }
}

export async function moveMouseTo(addr, x, y, duration, tween) {
  const url = new URL("/move_mouse_to", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ x, y, duration, tween }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to move mouse", error);
  }
}

export async function click(addr, button) {
  const url = new URL("/click", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ button }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to click", error);
  }
}

export async function doubleClick(addr) {
  const url = new URL("/double_click", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to double click", error);
  }
}

export async function typeText(addr, text, minInterval, maxInterval) {
  const url = new URL("/type_text", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text, minInterval, maxInterval }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to type text", error);
  }
}

export async function pressKey(addr, key) {
  const url = new URL("/press_key", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ key }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to press key", error);
  }
}

export async function scroll(addr, clicks) {
  const url = new URL("/scroll", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ clicks }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to scroll", error);
  }
}

export async function dragMouse(addr, x, y) {
  const url = new URL("/drag_mouse", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ x, y }),
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to drag mouse", error);
  }
}

export async function takeScreenshot(addr) {
  const url = new URL("/screenshot", addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to take screenshot", error);
  }
}

export async function listRecordings(addr) {
  const url = new URL("/recordings", addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to list recordings", error);
  }
}

export async function stopRecording(addr, sessionId) {
  const url = new URL(`/recordings/${sessionId}/stop`, addr);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to stop recording", error);
  }
}

export async function getRecording(addr, sessionId) {
  const url = new URL(`/recordings/${sessionId}`, addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to get recording", error);
  }
}

export async function startRecording(addr, description) {
  const url = new URL("/recordings", addr);
  console.log("starting recording at URL: ", url);

  try {
    const resp = await fetch(url, {
      method: "POST",
      cache: "no-cache",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ description }),
      redirect: "follow",
    });
    if (!resp.ok) {
      throw new Error("HTTP status " + resp.status);
    }
    const data = await resp.json();
    console.log("start recording data: ", data);
    return data;
  } catch (error) {
    console.error("Failed to start recording", error);
  }
}

export async function getEvent(addr, sessionId, eventId) {
  const url = new URL(`/recordings/${sessionId}/event/${eventId}`, addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to get event", error);
  }
}

export async function deleteEvent(addr, sessionId, eventId) {
  const url = new URL(`/recordings/${sessionId}/event/${eventId}`, addr);
  try {
    const response = await fetch(url, {
      method: "DELETE",
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to delete event", error);
  }
}

export async function listActiveSessions(addr) {
  const url = new URL("/active_sessions", addr);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    return await response.json();
  } catch (error) {
    console.error("Failed to list active sessions", error);
  }
}

export async function getActions(addr, sessionId) {
  const url = new URL(`/recordings/${sessionId}/actions`, addr);

  try {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error("HTTP status " + response.status);
    }
    const data = await response.json();
    console.log("Actions data:", data);
    return data;
  } catch (error) {
    console.error("Failed to fetch actions data", error);
  }
}
