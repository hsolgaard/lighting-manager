# Lighting Manager

**Manage your lights in one place, not in five.**

Lighting Manager is a Home Assistant integration for the everyday jobs that
Home Assistant spreads across Settings, dashboards and separate add-ons:
naming a light, putting it in a room, keeping it tidy, setting a timer, adding
a schedule, and swapping a bulb for a new one without breaking anything.

It adds a **Lighting Manager** panel to your sidebar, two ready-made pages
(**Lights** and **Schedules**), and a set of dashboard cards. There is no YAML
to write to get started.

<img width="1173" height="672" alt="image" src="https://github.com/user-attachments/assets/0e4c70ea-7761-4e3c-af78-1a7ec8e6c91e" />

- **One list of every light**, with rooms, types, icons and what is on now.
- **New lights are never added without you.** A new light waits in an Inbox
  until you choose its name, room and type.
- **Ready-made pages** for the whole house: everything that is on, every light
  by room, and every room's schedules. Anyone in the house can use them.
- **Countdowns** ("on for 30 minutes") built in, on any light tile.
- **Schedules by room**, using the popular Scheduler add-on (optional, see
  [Requirements](#requirements)).
- **Replace a light** (a dead bulb, or an upgrade such as Wi-Fi to Zigbee) and
  its name, room, schedules and settings carry over to the new one.

> Lighting Manager is new. It has been tested on Home Assistant 2026.7. Please
> [open an issue](https://github.com/hsolgaard/lighting-manager/issues) if
> something does not behave as described here.

## Requirements

**Needed:** Home Assistant 2025.1 or newer, and the lights you want to manage
already set up in Home Assistant (Zigbee, Wi-Fi, Hue, anything that gives you a
`light` entity).

**Optional, for schedules:** two separate projects by Niels Faber. They are
third-party, not part of Lighting Manager, and both are available in HACS.
Without them everything else in Lighting Manager works; the schedule parts just
stay empty.

| Install | What it does | Where |
|---|---|---|
| **Scheduler Component** | The part that stores and runs your schedules. | [nielsfaber/scheduler-component](https://github.com/nielsfaber/scheduler-component) |
| **Scheduler Card** | The screen where you add and edit schedules. Lighting Manager shows it inside its own pages. | [nielsfaber/scheduler-card](https://github.com/nielsfaber/scheduler-card) |

Install both from HACS (search for "Scheduler"), and restart Home Assistant
after installing the component.

## Install

### With HACS

1. In HACS, open the three-dot menu, then **Custom repositories**.
2. Paste `https://github.com/hsolgaard/lighting-manager`, choose the type
   **Integration**, and add it.
3. Find **Lighting Manager** in HACS and download it.
4. Restart Home Assistant.

(Once Lighting Manager is in HACS's default list you can skip steps 1 and 2 and
just search for it.)

### Manually

Copy the `custom_components/lighting_manager` folder from this repository into
the `custom_components` folder of your Home Assistant configuration, then
restart Home Assistant.

## Set up (about five minutes)

1. Go to **Settings, Devices & services, Add integration**, search for
   **Lighting Manager** and select it. There are no questions to answer.
2. Open **Lighting Manager** in the sidebar and click the **Inbox** tab. Every
   light Home Assistant knows about is waiting there, because nothing is added
   automatically.
3. For each light, click **Review**. A panel opens on the right. Choose a
   **name**, a **room** (a Home Assistant Area) and, if you like, a **type**
   (ceiling, lamp, spotlights...). Then click **Adopt**. Click **Ignore** for
   anything you do not want managed, such as a light that belongs to someone
   else or a switch that is not a lamp. You can undo Ignore later.

<img width="1210" height="765" alt="image" src="https://github.com/user-attachments/assets/979b03f1-a220-4149-9af8-7bfbbac82ce9" />


4. When you are done, open the **Lights (Lighting Manager)** page in the
   sidebar. Your whole house is there.

Lights are adopted one at a time for now. A bulk "adopt many at once" screen is
planned.

## The pages

### Lights (Lighting Manager)

Everything on one page: what is on right now (with **Turn all off**), then every
light by room with on/off buttons for the whole room. Tap a light to turn it on
or off. Double-tap it for the countdown (below). Under each room there is a
folded **Schedules** row; open it to see that room's schedules or add one.

<img width="603" height="1218" alt="Lights" src="https://github.com/user-attachments/assets/d5c90f9b-c35d-4843-8f08-ba7eae5caca4" />

### Schedules (Lighting Manager)

One schedule list per room, for everyone in the house. Each room has a button
with a calendar and plus icon, **Add or edit room schedules**.

<img width="1119" height="532" alt="image" src="https://github.com/user-attachments/assets/ed267397-722b-4f26-894a-7f099287f360" />


### Turning the pages off

They are on by default. To remove them from the sidebar for everyone, go to
**Settings, Devices & services, Lighting Manager, Configure** and untick
**Show the Lights and Schedules pages in the sidebar**. This takes effect
straight away. Each person can also hide any sidebar entry for themselves in
their profile. The cards below stay available either way.

<img width="1074" height="532" alt="image" src="https://github.com/user-attachments/assets/82dc6ead-498e-405e-8c98-74a1da33b894" />


## Everyday jobs

### Add a new light

1. Add the bulb to Home Assistant the usual way (pair it in ZHA, Zigbee2MQTT,
   Hue, and so on).
2. Open **Lighting Manager, Inbox**. The new light is there.
3. **Review**, choose its name, room and type, and click **Adopt**.

It now appears on the Lights page, in its room, by itself.

### Add a schedule

You need the two Scheduler installs above.

1. Open **Schedules (Lighting Manager)** and find the room.
2. Click the **Add or edit room schedules** button (calendar with a plus).
3. Use the **+** in the schedule editor, choose the lights, the time and the
   days, and save.

You can also do this from the folded **Schedules** row under a room on the
Lights page. A light's schedule count shows in the Lighting Manager table.

### Set a countdown (on for 30 minutes, off in an hour)

Double-tap any light tile. Pick 15, 30, 60 or 120 minutes, or type your own.
The tile shows a small timer while it runs, and you can change or cancel it
from the same popup. A countdown is cancelled if someone else turns the light
off, so it never fights you. Countdowns survive a restart.

<img width="952" height="543" alt="image" src="https://github.com/user-attachments/assets/6ae6773e-f378-4bfd-a876-e189d43b873c" />



### Replace a light

Swapping a dead bulb, or moving from one technology to another (Wi-Fi to
Zigbee, say)?

1. Add the new bulb to Home Assistant. It appears in the Inbox.
2. In the Inbox, click **Replaces...** next to the new bulb. (Or open the old
   light in Lighting Manager and click **Replace this light...**.)
3. Choose the old light, read the preview, and confirm.

The new light takes over the old light's **entity ID, name, room, icon, type
and settings**, so every schedule, automation, scene and dashboard that used
the old light keeps working with no edits. A dead old light is removed. An old
light that still works is **disabled and renamed** (for example
`light.kitchen_replaced`) rather than deleted, so its integration cannot
re-create it as a new light after a restart. Delete its device later when you
are ready.

<img width="535" height="317" alt="image" src="https://github.com/user-attachments/assets/01907c46-4ba5-45ff-b493-73d05f41485e" />



### Change the order of lights on a dashboard

Lighting Manager, **Rooms** tab, then the arrows next to each light. The order
is shared by every Lighting Manager card.

<img width="510" height="292" alt="image" src="https://github.com/user-attachments/assets/597fb40f-a9c7-4dbd-9d7c-aacd62540f17" />


### Other settings in the light's panel

Rename, change room, change type, choose an **icon**, include or exclude the
light from the Area-wide schedule list, show a **brightness slider** under its
tile, and choose whether its tile is **half or full width**.

## Dashboard cards (optional)

The two ready-made pages cover most households. If you also want the same
pieces on your own dashboards, add the cards below. They follow Lighting
Manager automatically: adopt a light or rename a room and they update.

Home Assistant waits for dashboard resources before it draws a dashboard, so
add this one resource once, which makes the cards load reliably. Go to
**Settings, Dashboards, three-dot menu, Resources, Add resource**:

```
URL:  /lighting_manager_static/lighting-manager-cards.js
Type: JavaScript module
```

(Lighting Manager also loads the cards by itself, but Home Assistant does not
wait for that, so on some page loads a card could show "Custom element doesn't
exist". The resource avoids it.)

**A whole lighting page from one card:**

<img width="1215" height="609" alt="image" src="https://github.com/user-attachments/assets/6071576d-4719-488b-9406-15f631cb5c61" />


```yaml
type: custom:lighting-manager-overview-card
title: Lights          # optional
columns: 2             # optional, 1 to 6
show_schedules: true   # optional
```

**One room:**

```yaml
type: custom:lighting-manager-room-card
area: living_room      # an Area id or its name
columns: 2             # optional
```

**One light, with the countdown popup:**

```yaml
type: custom:lighting-manager-countdown-card
entity: light.kitchen
```

**Schedules for one room** (needs the Scheduler installs):

```yaml
type: custom:light-scheduler-list-card
area: kitchen
```

## Turn off everything in a room

An action you can use in automations and scripts. It turns off every light in
the room, plus any switch you have adopted as a lamp, and never touches other
switches that happen to be in the same room.

```yaml
action: lighting_manager.turn_off_area
target:
  area_id: kitchen
```

## Lamps on smart plugs

A smart plug is a `switch`, not a `light`, so Lighting Manager never assumes it
is a lamp. In the Lighting Manager panel click **Add a switch as a light** and
choose it. It then appears in the Inbox like any other light.

## Troubleshooting

**A dashboard card says "Custom element doesn't exist."** Add the dashboard
resource from the Dashboard cards section above, then hard-refresh the page
(Cmd+Shift+R on a Mac, Ctrl+F5 on Windows).

**The Lights and Schedules pages are not in the sidebar.** Check Settings,
Devices & services, Lighting Manager, Configure: the box must be ticked. Also
check your own profile has not hidden them.

**Schedules are empty or the Schedules row is blank.** Install the Scheduler
Component and the Scheduler Card from HACS (see Requirements) and restart.

**A light is not in the Inbox.** It may already be adopted or ignored. Turn on
**Show ignored** in the toolbar to see ignored lights.

**A light has "unassigned" next to it.** It has no room. Open it and choose
one. Rooms are Home Assistant Areas; create them in Settings, Areas.

**The pages did not change after I updated.** Restart Home Assistant, then
hard-refresh the browser. Lighting Manager's files are served so that a restart
is enough to pick up a new version.

## Removing Lighting Manager

Remove the integration in Settings, Devices & services, then delete it in HACS
(or delete the `custom_components/lighting_manager` folder) and restart. Your
lights, names, rooms and schedules stay exactly as they are in Home Assistant:
Lighting Manager keeps only its own settings, such as which lights it manages.
If you added the dashboard resource, remove that too.

## Good to know

- Lighting Manager changes things in your Home Assistant when you ask it to
  (names, rooms, icons and type labels are saved in Home Assistant's own
  registries, so they show everywhere, not just here). Only administrators can
  make changes. Everyone else can view the pages and start or cancel a
  countdown.
- A light's **type** is stored as a Home Assistant Label named like
  "Lighting: Ceiling".
- Schedules are provided by the Scheduler Component. Lighting Manager does not
  run its own scheduler.

## Contributing and development

Bug reports and ideas are welcome in
[Issues](https://github.com/hsolgaard/lighting-manager/issues). Developer notes
and how to run the tests are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
