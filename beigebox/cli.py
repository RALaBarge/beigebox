    # operator / op
    def setup_operator(p):
        p.add_argument("query", nargs="*", help="Question to ask (omit for interactive REPL)")
        
    _add_command(sub, ["operator", "op"], 
                 "Launch the Operator agent (web, data, shell)", 
                 cmd_operator, 
                 setup_operator)

    args = parser.parse_args()
    if not args.command:
        cmd_tone(args)
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
