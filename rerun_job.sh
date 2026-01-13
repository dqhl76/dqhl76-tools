#!/bin/bash

export GH_PAGER=cat

RUN_ID="20948622518"
JOB_NAME="linux / sqllogic / standalone (tpch, 2c, hybrid)"

echo "Starting continuous rerun monitor for: $JOB_NAME"

while true; do
    echo "=== $(date) ==="
    
    # Get the latest job ID for this job name
    JOB_ID=$(gh run view "$RUN_ID" --json jobs --jq ".jobs[] | select(.name == \"$JOB_NAME\") | .databaseId" | tail -1)
    
    if [ -z "$JOB_ID" ]; then
        echo "❌ Failed to find job with name: $JOB_NAME"
        exit 1
    fi
    
    echo "Current job ID: $JOB_ID"
    echo "Triggering rerun..."
    
    gh api --method POST "/repos/databendlabs/databend/actions/jobs/$JOB_ID/rerun"
    
    if [ $? -ne 0 ]; then
        echo "❌ Failed to trigger rerun. Retrying in 5 seconds..."
        sleep 5
        continue
    fi
    
    echo "Waiting for new job to be created..."
    sleep 15
    
    # Get the new job ID after rerun
    NEW_JOB_ID=$(gh run view "$RUN_ID" --json jobs --jq ".jobs[] | select(.name == \"$JOB_NAME\") | .databaseId" | tail -1)
    echo "New job ID: $NEW_JOB_ID"
    
    # Monitor job status
    while true; do
        # Try to get job status with retry on API error
        JOB_DATA=$(gh run view "$RUN_ID" --json jobs --jq ".jobs[] | select(.databaseId == $NEW_JOB_ID)" 2>&1)
        
        if [ $? -ne 0 ]; then
            echo "⚠️  API error, retrying in 10 seconds..."
            sleep 10
            continue
        fi
        
        STATUS=$(echo "$JOB_DATA" | jq -r '.status')
        CONCLUSION=$(echo "$JOB_DATA" | jq -r '.conclusion')
        
        # Handle null/empty conclusion
        if [ "$CONCLUSION" = "null" ] || [ -z "$CONCLUSION" ]; then
            CONCLUSION="(none)"
        fi
        
        echo "Status: $STATUS | Conclusion: $CONCLUSION"
        
        if [ "$STATUS" = "completed" ]; then
            if [ "$CONCLUSION" = "failure" ]; then
                echo ""
                echo "🚨🚨🚨 JOB FAILED! 🚨🚨🚨"
                echo "Job ID: $NEW_JOB_ID"
                echo "Run ID: $RUN_ID"
                echo "Time: $(date)"
                echo "URL: https://github.com/databendlabs/databend/actions/runs/$RUN_ID/job/$NEW_JOB_ID"
                echo ""
                
                # Try to make a sound notification (macOS)
                for i in {1..3}; do
                    afplay /System/Library/Sounds/Sosumi.aiff 2>/dev/null
                    sleep 1
                done
                
                exit 1
            elif [ "$CONCLUSION" = "success" ]; then
                echo "✅ Job completed successfully. Rerunning in 5 seconds..."
                sleep 5
                break
            else
                echo "⚠️  Job completed with unexpected conclusion: $CONCLUSION. Stopping."
                exit 1
            fi
        fi
        
        sleep 15
    done
    
    echo ""
done
