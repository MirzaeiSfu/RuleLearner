package ca.sfu.cs.bnrunner.app;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import ca.sfu.cs.factorbase.data.DataExtractor;
import ca.sfu.cs.factorbase.data.TSVDataExtractor;
import ca.sfu.cs.factorbase.exception.DataExtractionException;
import ca.sfu.cs.factorbase.exception.ScoringException;
import ca.sfu.cs.factorbase.graph.Edge;
import ca.sfu.cs.factorbase.jbn.BayesNet_Learning_main;

/**
 * Standalone CLI for BN structure learning so Python can call the learner as an external jar.
 */
public class RunBNLearner {
    private static final String DEFAULT_COUNTS_COLUMN = "MULT";

    public static void main(String[] args) throws IOException, DataExtractionException, ScoringException {
        Map<String, String> options = parseArgs(args);

        String inputTSV = options.get("input-tsv");
        String outputEdges = options.get("output-edges");

        if (inputTSV == null || outputEdges == null) {
            printUsageAndExit(1);
        }

        String countsColumn = options.getOrDefault("counts-column", DEFAULT_COUNTS_COLUMN);
        boolean isDiscrete = Boolean.parseBoolean(options.getOrDefault("discrete", "true"));
        List<Edge> requiredEdges = loadEdges(options.get("required-edges"));
        List<Edge> forbiddenEdges = loadEdges(options.get("forbidden-edges"));

        DataExtractor dataExtractor = new TSVDataExtractor(inputTSV, countsColumn, isDiscrete);
        List<Edge> learnedEdges = BayesNet_Learning_main.tetradLearner(
            dataExtractor,
            requiredEdges,
            forbiddenEdges,
            isDiscrete
        );

        writeEdges(outputEdges, learnedEdges);
    }


    private static void printUsageAndExit(int status) {
        System.err.println("Usage:");
        System.err.println("  java -jar bnrunner-1.0-SNAPSHOT.jar --input-tsv <path> --output-edges <path>");
        System.err.println("       [--counts-column MULT] [--discrete true|false]");
        System.err.println("       [--required-edges <path>] [--forbidden-edges <path>]");
        System.exit(status);
    }


    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> options = new HashMap<String, String>();

        int index = 0;
        while (index < args.length) {
            String argument = args[index];
            if (!argument.startsWith("--")) {
                printUsageAndExit(1);
            }

            String key = argument.substring(2);
            if (index + 1 >= args.length || args[index + 1].startsWith("--")) {
                printUsageAndExit(1);
            }

            options.put(key, args[index + 1]);
            index += 2;
        }

        return options;
    }


    private static List<Edge> loadEdges(String edgeFilePath) throws IOException {
        if (edgeFilePath == null || edgeFilePath.trim().isEmpty()) {
            return null;
        }

        List<Edge> edges = new ArrayList<Edge>();
        try (BufferedReader reader = new BufferedReader(new FileReader(edgeFilePath))) {
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }

                String[] values;
                if (line.contains("\t")) {
                    values = line.split("\t", -1);
                } else {
                    values = line.split(",", -1);
                }

                if (values.length < 2) {
                    continue;
                }

                String parent = values[0].trim();
                String child = values[1].trim();

                if (("parent".equalsIgnoreCase(parent) && "child".equalsIgnoreCase(child)) || child.isEmpty()) {
                    continue;
                }

                edges.add(new Edge(parent, child));
            }
        }

        return edges;
    }


    private static void writeEdges(String outputPath, List<Edge> edges) throws IOException {
        File outFile = new File(outputPath);
        File parentDir = outFile.getParentFile();
        if (parentDir != null) {
            parentDir.mkdirs();
        }

        try (BufferedWriter writer = new BufferedWriter(new FileWriter(outFile))) {
            writer.write("parent\tchild");
            writer.newLine();
            for (Edge edge : edges) {
                writer.write(edge.getParent());
                writer.write("\t");
                writer.write(edge.getChild());
                writer.newLine();
            }
        }
    }
}
