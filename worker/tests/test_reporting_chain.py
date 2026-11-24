from scene.palette import type as typography


def test_word_timed_levels_and_attributed_counts():
    result = typography.reporting_chain("account", 180,
        [(20,"Person"),(40,"Manager"),(60,"Next manager")],
        [(80,"3",None),(100,"5",None),(120,"4",40),(140,"5",None)], 160,
        width=1080, height=1920, voice=dict(font="InterDisplay-Bold.ttf",px=48),
        ink="#FFFFFF",accent="#FFCC00")
    items = {i["id"].removeprefix("account."): i for i in result["items"]}
    levels = [items[f"level{i}"] for i in range(3)]
    assert [it["f"] for it in levels] == [[20,180],[40,180],[60,180]]
    assert levels[1]["y"] < levels[0]["y"]
    assert [items[f"link{i}"]["f"] for i in (1,2)] == [[40,180],[60,180]]
    quoted = [items[f"count{i}"] for i in (0,1,3)]
    assert [it["f"] for it in quoted] == [[80,160],[100,160],[140,160]]
    assert [it["t"] for it in quoted] == ["3","5","5"]
    assert len({it["y"] for it in quoted}) == 1
    assert items["count2"]["y"] == levels[1]["y"]
    assert items["open-quote"]["f"] == [80,160]
    assert items["close-quote0"]["f"] == [80,100]
    assert items["flat"]["f"] == [160,180]
    assert [items[f"flat-level{i}"]["t"] for i in range(2)] == ["Team","Manager"]
    assert items["flat-level1"]["y"] == levels[1]["y"]
