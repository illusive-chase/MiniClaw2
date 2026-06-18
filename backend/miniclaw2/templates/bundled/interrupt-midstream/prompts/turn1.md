Use the Bash tool to run exactly this command — stream the output as
it arrives, do not buffer:

    for i in $(seq 1 60); do echo "line $i"; sleep 1; done

You should be running this command for about a minute. I will hit the
Stop button before it finishes. Do not summarise the output; just run
the command.
